# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Handle Thrift requests.
"""


import base64
from collections import defaultdict
from datetime import datetime, timedelta
import os
import re
import shlex
import tempfile
import time
import zipfile
import zlib

import sqlalchemy
from sqlalchemy.sql.expression import or_, and_, not_, func, \
    asc, desc, union_all, select, bindparam, literal_column, cast

import codechecker_api_shared
from codechecker_api.codeCheckerDBAccess_v6 import constants, ttypes
from codechecker_api.codeCheckerDBAccess_v6.ttypes import BugPathPos, \
    CheckerCount, CommentData, DiffType, Encoding, RunHistoryData, Order, \
    ReportData, ReportDetails, ReviewData, RunData, RunFilter, \
    RunReportCount, RunSortType, RunTagCount, SourceComponentData, \
    SourceFileData, SortMode, SortType

from codechecker_common import plist_parser, skiplist_handler
from codechecker_common.source_code_comment_handler import \
    SourceCodeCommentHandler, SpellException, contains_codechecker_comment
from codechecker_common import util
from codechecker_common.logger import get_logger
from codechecker_report_hash.hash import get_report_path_hash

from codechecker_web.shared import webserver_context
from codechecker_web.shared import convert

from codechecker_server.profiler import timeit

from .. import permissions
from ..database import db_cleanup
from ..database.config_db_model import Product
from ..database.database import conv, DBSession, escape_like
from ..database.run_db_model import \
    AnalyzerStatistic, Report, ReviewStatus, File, Run, RunHistory, \
    RunLock, Comment, BugPathEvent, BugReportPoint, \
    FileContent, SourceComponent, ExtendedReportData
from ..metadata import MetadataInfoParser
from ..tmp import TemporaryDirectory

from .thrift_enum_helper import detection_status_enum, \
    detection_status_str, review_status_enum, review_status_str, \
    report_extended_data_type_enum

from . import store_handler

LOG = get_logger('server')


class CommentKindValue(object):
    USER = 0
    SYSTEM = 1


def comment_kind_from_thrift_type(kind):
    """ Convert the given comment kind from Thrift type to Python enum. """
    if kind == ttypes.CommentKind.USER:
        return CommentKindValue.USER
    elif kind == ttypes.CommentKind.SYSTEM:
        return CommentKindValue.SYSTEM


def comment_kind_to_thrift_type(kind):
    """ Convert the given comment kind from Python enum to Thrift type. """
    if kind == CommentKindValue.USER:
        return ttypes.CommentKind.USER
    elif kind == CommentKindValue.SYSTEM:
        return ttypes.CommentKind.SYSTEM


def verify_limit_range(limit):
    """Verify limit value for the queries.

    Query limit should not be larger than the max allowed value.
    Max is returned if the value is larger than max.
    """
    max_query_limit = constants.MAX_QUERY_SIZE
    if not limit:
        return max_query_limit
    if limit > max_query_limit:
        LOG.warning('Query limit %d was larger than max query limit %d, '
                    'setting limit to %d',
                    limit,
                    max_query_limit,
                    max_query_limit)
        limit = max_query_limit
    return limit


def slugify(text):
    """
    Removes and replaces special characters in a given text.
    """
    # Removes non-alpha characters.
    norm_text = re.sub(r'[^\w\s\-/]', '', text)

    # Converts spaces and slashes to underscores.
    norm_text = re.sub(r'([\s]+|[/]+)', '_', norm_text)

    return norm_text


def exc_to_thrift_reqfail(func):
    """
    Convert internal exceptions to RequestFailed exception
    which can be sent back on the thrift connections.
    """
    func_name = func.__name__

    def wrapper(*args, **kwargs):
        try:
            res = func(*args, **kwargs)
            return res

        except sqlalchemy.exc.SQLAlchemyError as alchemy_ex:
            # Convert SQLAlchemy exceptions.
            msg = str(alchemy_ex)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)
        except codechecker_api_shared.ttypes.RequestFailed as rf:
            LOG.warning("%s:\n%s", func_name, rf.message)
            raise
        except Exception as ex:
            msg = str(ex)
            LOG.warning("%s:\n%s", func_name, msg)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.GENERAL, msg)

    return wrapper


def parse_codechecker_review_comment(source_file_name,
                                     report_line,
                                     checker_name):
    """Parse the CodeChecker review comments from a source file at a given
    position.  Returns an empty list if there are no comments.
    """
    src_comment_data = []
    with open(source_file_name,
              encoding='utf-8',
              errors='ignore') as sf:
        if contains_codechecker_comment(sf):
            sc_handler = SourceCodeCommentHandler()
            try:
                src_comment_data = sc_handler.filter_source_line_comments(
                    sf,
                    report_line,
                    checker_name)
            except SpellException as ex:
                LOG.warning(f"File {source_file_name} contains {ex}")
    return src_comment_data


def get_component_values(session, component_name):
    """
    Get component values by component names and returns a tuple where the
    first item contains a list path which should be skipped and the second
    item contains a list of path which should be included.
    E.g.:
      +/a/b/x.cpp
      +/a/b/y.cpp
      -/a/b
    On the above component value this function will return the following:
      (['/a/b'], ['/a/b/x.cpp', '/a/b/y.cpp'])
    """
    components = session.query(SourceComponent) \
        .filter(SourceComponent.name.like(component_name)) \
        .all()

    skip = []
    include = []

    for component in components:
        values = component.value.decode('utf-8').split('\n')
        for value in values:
            value = value.strip()
            if not value:
                continue

            v = value[1:]
            if value[0] == '+':
                include.append(v)
            elif value[0] == '-':
                skip.append(v)

    return skip, include


def process_report_filter(session, run_ids, report_filter, cmp_data=None):
    """
    Process the new report filter.
    """
    AND = []

    cmp_filter_expr = process_cmp_data_filter(session, run_ids, report_filter,
                                              cmp_data)
    if cmp_filter_expr is not None:
        AND.append(cmp_filter_expr)

    if report_filter is None:
        return and_(*AND)

    if report_filter.filepath:
        OR = [File.filepath.ilike(conv(fp))
              for fp in report_filter.filepath]

        AND.append(or_(*OR))

    if report_filter.checkerMsg:
        OR = [Report.checker_message.ilike(conv(cm))
              for cm in report_filter.checkerMsg]
        AND.append(or_(*OR))

    if report_filter.checkerName:
        OR = [Report.checker_id.ilike(conv(cn))
              for cn in report_filter.checkerName]
        AND.append(or_(*OR))

    if report_filter.analyzerNames:
        OR = [Report.analyzer_name.ilike(conv(an))
              for an in report_filter.analyzerNames]
        AND.append(or_(*OR))

    if report_filter.runName:
        OR = [Run.name.ilike(conv(rn))
              for rn in report_filter.runName]
        AND.append(or_(*OR))

    if report_filter.reportHash:
        OR = []
        no_joker = []

        for rh in report_filter.reportHash:
            if '*' in rh:
                OR.append(Report.bug_id.ilike(conv(rh)))
            else:
                no_joker.append(rh)

        if no_joker:
            OR.append(Report.bug_id.in_(no_joker))

        AND.append(or_(*OR))

    if report_filter.severity:
        AND.append(Report.severity.in_(report_filter.severity))

    if report_filter.detectionStatus:
        dst = list(map(detection_status_str,
                       report_filter.detectionStatus))
        AND.append(Report.detection_status.in_(dst))

    if report_filter.reviewStatus:
        OR = [ReviewStatus.status.in_(
            list(map(review_status_str, report_filter.reviewStatus)))]

        # No database entry for unreviewed reports
        if (ttypes.ReviewStatus.UNREVIEWED in
                report_filter.reviewStatus):
            OR.append(ReviewStatus.status.is_(None))

        AND.append(or_(*OR))

    if report_filter.firstDetectionDate is not None:
        date = datetime.fromtimestamp(report_filter.firstDetectionDate)
        AND.append(Report.detected_at >= date)

    if report_filter.fixDate is not None:
        date = datetime.fromtimestamp(report_filter.fixDate)
        AND.append(Report.detected_at < date)

    if report_filter.date:
        detected_at = report_filter.date.detected
        if detected_at:
            if detected_at.before:
                detected_before = datetime.fromtimestamp(detected_at.before)
                AND.append(Report.detected_at <= detected_before)

            if detected_at.after:
                detected_after = datetime.fromtimestamp(detected_at.after)
                AND.append(Report.detected_at >= detected_after)

        fixed_at = report_filter.date.fixed
        if fixed_at:
            if fixed_at.before:
                fixed_before = datetime.fromtimestamp(fixed_at.before)
                AND.append(Report.fixed_at <= fixed_before)

            if fixed_at.after:
                fixed_after = datetime.fromtimestamp(fixed_at.after)
                AND.append(Report.fixed_at >= fixed_after)

    if report_filter.runHistoryTag:
        OR = []
        for history_date in report_filter.runHistoryTag:
            date = datetime.strptime(history_date,
                                     '%Y-%m-%d %H:%M:%S.%f')
            OR.append(and_(Report.detected_at <= date, or_(
                Report.fixed_at.is_(None), Report.fixed_at >= date)))
        AND.append(or_(*OR))

    if report_filter.componentNames:
        OR = []

        for component_name in report_filter.componentNames:
            skip, include = get_component_values(session, component_name)

            if skip and include:
                include_q = select([File.id]) \
                    .where(or_(*[
                        File.filepath.like(conv(fp)) for fp in include])) \
                    .distinct()

                skip_q = select([File.id]) \
                    .where(or_(*[
                        File.filepath.like(conv(fp)) for fp in skip])) \
                    .distinct()

                OR.append(or_(File.id.in_(
                    include_q.except_(skip_q))))
            elif include:
                include_q = [File.filepath.like(conv(fp)) for fp in include]
                OR.append(or_(*include_q))
            elif skip:
                skip_q = [not_(File.filepath.like(conv(fp))) for fp in skip]
                OR.append(and_(*skip_q))

        AND.append(or_(*OR))

    if report_filter.bugPathLength is not None:
        min_path_length = report_filter.bugPathLength.min
        if min_path_length is not None:
            AND.append(Report.path_length >= min_path_length)

        max_path_length = report_filter.bugPathLength.max
        if max_path_length is not None:
            AND.append(Report.path_length <= max_path_length)

    filter_expr = and_(*AND)
    return filter_expr


def get_open_reports_date_filter_query(tbl=Report, date=RunHistory.time):
    """ Get open reports date filter. """
    return and_(tbl.detected_at <= date,
                or_(tbl.fixed_at.is_(None),
                    tbl.fixed_at > date))


def get_diff_bug_id_query(session, run_ids, tag_ids, open_reports_date):
    """ Get bug id query for diff. """
    q = session.query(Report.bug_id.distinct())
    if run_ids:
        q = q.filter(Report.run_id.in_(run_ids))

    if tag_ids:
        q = q.outerjoin(RunHistory,
                        RunHistory.run_id == Report.run_id) \
             .filter(RunHistory.id.in_(tag_ids)) \
             .filter(get_open_reports_date_filter_query())

    if open_reports_date:
        date = datetime.fromtimestamp(open_reports_date)

        q = q.filter(get_open_reports_date_filter_query(Report, date))

    return q


def get_diff_run_id_query(session, run_ids, tag_ids):
    """ Get run id query for diff. """
    q = session.query(Run.id.distinct())

    if run_ids:
        q = q.filter(Run.id.in_(run_ids))

    if tag_ids:
        q = q.outerjoin(RunHistory,
                        RunHistory.run_id == Run.id) \
             .filter(RunHistory.id.in_(tag_ids))

    return q


def is_cmp_data_empty(cmp_data):
    """ True if the parameter is None or no filter fields are set. """
    if not cmp_data:
        return True

    return not any([cmp_data.runIds,
                    cmp_data.runTag,
                    cmp_data.openReportsDate])


def process_cmp_data_filter(session, run_ids, report_filter, cmp_data):
    """ Process compare data filter. """
    base_tag_ids = report_filter.runTag if report_filter else None
    base_open_reports_date = report_filter.openReportsDate \
        if report_filter else None
    query_base = get_diff_bug_id_query(session, run_ids, base_tag_ids,
                                       base_open_reports_date)
    query_base_runs = get_diff_run_id_query(session, run_ids, base_tag_ids)

    if is_cmp_data_empty(cmp_data):
        if not run_ids and (not report_filter or not report_filter.runTag):
            return None

        return and_(Report.bug_id.in_(query_base),
                    Report.run_id.in_(query_base_runs))

    query_new = get_diff_bug_id_query(session, cmp_data.runIds,
                                      cmp_data.runTag,
                                      cmp_data.openReportsDate)
    query_new_runs = get_diff_run_id_query(session, cmp_data.runIds,
                                           cmp_data.runTag)

    AND = []
    if cmp_data.diffType == DiffType.NEW:
        return and_(Report.bug_id.in_(query_new.except_(query_base)),
                    Report.run_id.in_(query_new_runs))

    elif cmp_data.diffType == DiffType.RESOLVED:
        return and_(Report.bug_id.in_(query_base.except_(query_new)),
                    Report.run_id.in_(query_base_runs))

    elif cmp_data.diffType == DiffType.UNRESOLVED:
        return and_(Report.bug_id.in_(query_base.intersect(query_new)),
                    Report.run_id.in_(query_new_runs))

    else:
        raise codechecker_api_shared.ttypes.RequestFailed(
            codechecker_api_shared.ttypes.ErrorCode.DATABASE,
            'Unsupported diff type: ' + str(cmp_data.diffType))

    return and_(*AND)


def process_run_history_filter(query, run_ids, run_history_filter):
    """
    Process run history filter.
    """
    if run_ids:
        query = query.filter(RunHistory.run_id.in_(run_ids))

    if run_history_filter and run_history_filter.tagNames:
        OR = [RunHistory.version_tag.ilike('{0}'.format(conv(
              escape_like(name, '\\'))), escape='\\') for
              name in run_history_filter.tagNames]

        query = query.filter(or_(*OR))

    if run_history_filter and run_history_filter.tagIds:
        query = query.filter(RunHistory.id.in_(run_history_filter.tagIds))

    return query


def process_run_filter(session, query, run_filter):
    """
    Process run filter.
    """
    if run_filter is None:
        return query

    if run_filter.ids:
        query = query.filter(Run.id.in_(run_filter.ids))
    if run_filter.names:
        if run_filter.exactMatch:
            query = query.filter(Run.name.in_(run_filter.names))
        else:
            OR = [Run.name.ilike('{0}'.format(conv(
                escape_like(name, '\\'))), escape='\\') for
                name in run_filter.names]
            query = query.filter(or_(*OR))

    if run_filter.beforeTime:
        date = datetime.fromtimestamp(run_filter.beforeTime)
        query = query.filter(Run.date < date)

    if run_filter.afterTime:
        date = datetime.fromtimestamp(run_filter.afterTime)
        query = query.filter(Run.date > date)

    if run_filter.beforeRun:
        run = session.query(Run.date) \
            .filter(Run.name == run_filter.beforeRun) \
            .one_or_none()

        if run:
            query = query.filter(Run.date < run.date)

    if run_filter.afterRun:
        run = session.query(Run.date) \
            .filter(Run.name == run_filter.afterRun) \
            .one_or_none()

        if run:
            query = query.filter(Run.date > run.date)

    return query


def get_report_details(session, report_ids):
    """
    Returns report details for the given report ids.
    """
    details = {}

    # Get bug path events.
    bug_path_events = session.query(BugPathEvent, File.filepath) \
        .filter(BugPathEvent.report_id.in_(report_ids)) \
        .outerjoin(File,
                   File.id == BugPathEvent.file_id) \
        .order_by(BugPathEvent.report_id, BugPathEvent.order)

    bug_events_list = defaultdict(list)
    for event, file_path in bug_path_events:
        report_id = event.report_id
        event = bugpathevent_db_to_api(event)
        event.filePath = file_path
        bug_events_list[report_id].append(event)

    # Get bug report points.
    bug_report_points = session.query(BugReportPoint, File.filepath) \
        .filter(BugReportPoint.report_id.in_(report_ids)) \
        .outerjoin(File,
                   File.id == BugReportPoint.file_id) \
        .order_by(BugReportPoint.report_id, BugReportPoint.order)

    bug_point_list = defaultdict(list)
    for bug_point, file_path in bug_report_points:
        report_id = bug_point.report_id
        bug_point = bugreportpoint_db_to_api(bug_point)
        bug_point.filePath = file_path
        bug_point_list[report_id].append(bug_point)

    # Get extended report data.
    extended_data_list = defaultdict(list)
    q = session.query(ExtendedReportData, File.filepath) \
        .filter(ExtendedReportData.report_id.in_(report_ids)) \
        .outerjoin(File,
                   File.id == ExtendedReportData.file_id)

    for data, file_path in q:
        report_id = data.report_id
        extended_data = extended_data_db_to_api(data)
        extended_data.filePath = file_path
        extended_data_list[report_id].append(extended_data)

    for report_id in report_ids:
        details[report_id] = \
            ReportDetails(pathEvents=bug_events_list[report_id],
                          executionPath=bug_point_list[report_id],
                          extendedData=extended_data_list[report_id])

    return details


def bugpathevent_db_to_api(bpe):
    return ttypes.BugPathEvent(
        startLine=bpe.line_begin,
        startCol=bpe.col_begin,
        endLine=bpe.line_end,
        endCol=bpe.col_end,
        msg=bpe.msg,
        fileId=bpe.file_id)


def bugreportpoint_db_to_api(brp):
    return BugPathPos(
        startLine=brp.line_begin,
        startCol=brp.col_begin,
        endLine=brp.line_end,
        endCol=brp.col_end,
        fileId=brp.file_id)


def extended_data_db_to_api(erd):
    return ttypes.ExtendedReportData(
        type=report_extended_data_type_enum(erd.type),
        startLine=erd.line_begin,
        startCol=erd.col_begin,
        endLine=erd.line_end,
        endCol=erd.col_end,
        message=erd.message,
        fileId=erd.file_id)


def unzip(b64zip, output_dir):
    """
    This function unzips the base64 encoded zip file. This zip is extracted
    to a temporary directory and the ZIP is then deleted. The function returns
    the size of the extracted zip file.
    """
    with tempfile.NamedTemporaryFile(suffix='.zip') as zip_file:
        LOG.debug("Unzipping mass storage ZIP '%s' to '%s'...",
                  zip_file.name, output_dir)

        zip_file.write(zlib.decompress(base64.b64decode(b64zip)))
        with zipfile.ZipFile(zip_file, 'r', allowZip64=True) as zipf:
            try:
                zipf.extractall(output_dir)
                return os.stat(zip_file.name).st_size
            except Exception:
                LOG.error("Failed to extract received ZIP.")
                import traceback
                traceback.print_exc()
                raise
    return 0


def create_review_data(review_status):
    if review_status:
        return ReviewData(status=review_status_enum(review_status.status),
                          comment=review_status.message.decode('utf-8'),
                          author=review_status.author,
                          date=str(review_status.date))
    else:
        return ReviewData(status=ttypes.ReviewStatus.UNREVIEWED)


def create_count_expression(report_filter):
    if report_filter is not None and report_filter.isUnique:
        return func.count(Report.bug_id.distinct())
    else:
        return func.count(literal_column('*'))


def apply_report_filter(q, filter_expression):
    """
    Applies the given filter expression and joins the File and ReviewStatus
    tables.
    """
    q = q.outerjoin(File,
                    Report.file_id == File.id) \
        .outerjoin(ReviewStatus,
                   ReviewStatus.bug_hash == Report.bug_id) \
        .filter(filter_expression)

    return q


def get_sort_map(sort_types, is_unique=False):
    # Get a list of sort_types which will be a nested ORDER BY.
    sort_type_map = {
        SortType.FILENAME: [(File.filepath, 'filepath'),
                            (Report.line, 'line')],
        SortType.BUG_PATH_LENGTH: [(Report.path_length, 'bug_path_length')],
        SortType.CHECKER_NAME: [(Report.checker_id, 'checker_id')],
        SortType.SEVERITY: [(Report.severity, 'severity')],
        SortType.REVIEW_STATUS: [(ReviewStatus.status, 'rw_status')],
        SortType.DETECTION_STATUS: [(Report.detection_status, 'dt_status')]}

    if is_unique:
        sort_type_map[SortType.FILENAME] = [(File.filename, 'filename')]
        sort_type_map[SortType.DETECTION_STATUS] = []

    # Mapping the SQLAlchemy functions.
    order_type_map = {Order.ASC: asc, Order.DESC: desc}

    if sort_types is None:
        sort_types = [SortMode(SortType.SEVERITY, Order.DESC)]

    return sort_types, sort_type_map, order_type_map


def sort_results_query(query, sort_types, sort_type_map, order_type_map,
                       order_by_label=False):
    """
    Helper method for __queryDiffResults and queryResults to apply sorting.
    """
    for sort in sort_types:
        sorttypes = sort_type_map.get(sort.type)
        for sorttype in sorttypes:
            order_type = order_type_map.get(sort.ord)
            sort_col = sorttype[1] if order_by_label else sorttype[0]
            query = query.order_by(order_type(sort_col))

    return query


def filter_unresolved_reports(q):
    """
    Filter reports which are unresolved.

    Note: review status of these reports are not in skip_review_statuses
    and detection statuses are not in skip_detection_statuses.
    """
    skip_review_statuses = ['false_positive', 'intentional']
    skip_detection_statuses = ['resolved', 'off', 'unavailable']

    return q.filter(Report.detection_status.notin_(skip_detection_statuses)) \
            .filter(or_(ReviewStatus.status.is_(None),
                        ReviewStatus.status.notin_(skip_review_statuses))) \
            .outerjoin(ReviewStatus,
                       ReviewStatus.bug_hash == Report.bug_id)


def check_remove_runs_lock(session, run_ids):
    """
    Check if there is an existing lock on the given runs, which has not
    expired yet. If so, the run cannot be deleted, as someone is assumed to
    be storing into it.
    """
    locks_expired_at = datetime.now() - timedelta(
        seconds=db_cleanup.RUN_LOCK_TIMEOUT_IN_DATABASE)

    run_locks = session.query(RunLock.name) \
        .filter(RunLock.locked_at >= locks_expired_at)

    if run_ids:
        run_locks = run_locks.filter(Run.id.in_(run_ids))

    run_locks = run_locks \
        .outerjoin(Run,
                   Run.name == RunLock.name) \
        .all()

    if run_locks:
        raise codechecker_api_shared.ttypes.RequestFailed(
            codechecker_api_shared.ttypes.ErrorCode.DATABASE,
            "Can not remove results because the following runs "
            "are locked: {0}".format(
                ', '.join([r[0] for r in run_locks])))


def sort_run_data_query(query, sort_mode):
    """
    Sort run data query by the given sort type.
    """
    # Sort by run date by default.
    if not sort_mode:
        return query.order_by(desc(Run.date))

    order_type_map = {Order.ASC: asc, Order.DESC: desc}
    order_type = order_type_map.get(sort_mode.ord)
    if sort_mode.type == RunSortType.NAME:
        query = query.order_by(order_type(Run.name))
    elif sort_mode.type == RunSortType.UNRESOLVED_REPORTS:
        query = query.order_by(order_type('report_count'))
    elif sort_mode.type == RunSortType.DATE:
        query = query.order_by(order_type(Run.date))
    elif sort_mode.type == RunSortType.DURATION:
        query = query.order_by(order_type(Run.duration))
    elif sort_mode.type == RunSortType.CC_VERSION:
        query = query.order_by(order_type(RunHistory.cc_version))

    return query


def escape_whitespaces(s, whitespaces=None):
    if not whitespaces:
        whitespaces = [' ', '\n', '\t', '\r']

    escaped = s
    for w in whitespaces:
        escaped = escaped.replace(w, '\\{0}'.format(w))

    return escaped


class ThriftRequestHandler(object):
    """
    Connect to database and handle thrift client requests.
    """

    def __init__(self,
                 manager,
                 Session,
                 product,
                 auth_session,
                 config_database,
                 checker_md_docs,
                 checker_md_docs_map,
                 package_version,
                 context):

        if not product:
            raise ValueError("Cannot initialize request handler without "
                             "a product to serve.")

        self.__manager = manager
        self.__product = product
        self.__auth_session = auth_session
        self.__config_database = config_database
        self.__checker_md_docs = checker_md_docs
        self.__checker_doc_map = checker_md_docs_map
        self.__package_version = package_version
        self.__Session = Session
        self.__context = context
        self.__permission_args = {
            'productID': product.id
        }

    def __get_username(self):
        """
        Returns the actually logged in user name.
        """
        return self.__auth_session.user if self.__auth_session else "Anonymous"

    def __require_permission(self, required):
        """
        Helper method to raise an UNAUTHORIZED exception if the user does not
        have any of the given permissions.
        """

        with DBSession(self.__config_database) as session:
            args = dict(self.__permission_args)
            args['config_db_session'] = session

            if not any([permissions.require_permission(
                            perm, args, self.__auth_session)
                        for perm in required]):
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.UNAUTHORIZED,
                    "You are not authorized to execute this action.")

            return True

    def __require_admin(self):
        self.__require_permission([permissions.PRODUCT_ADMIN])

    def __require_access(self):
        self.__require_permission([permissions.PRODUCT_ACCESS])

    def __require_store(self):
        self.__require_permission([permissions.PRODUCT_STORE])

    def __add_comment(self, bug_id, message, kind=CommentKindValue.USER):
        """ Creates a new comment object. """
        user = self.__get_username()
        return Comment(bug_id,
                       user,
                       message.encode('utf-8'),
                       kind,
                       datetime.now())

    @timeit
    def getRunData(self, run_filter, limit, offset, sort_mode):
        self.__require_access()

        limit = verify_limit_range(limit)

        with DBSession(self.__Session) as session:

            # Count the reports subquery.
            stmt = session.query(Report.run_id,
                                 func.count(Report.bug_id)
                                 .label('report_count'))

            stmt = filter_unresolved_reports(stmt) \
                .group_by(Report.run_id).subquery()

            tag_q = session.query(RunHistory.run_id,
                                  func.max(RunHistory.id).label(
                                      'run_history_id'),
                                  func.max(RunHistory.time).label(
                                      'run_history_time')) \
                .group_by(RunHistory.run_id) \
                .subquery()

            q = session.query(Run.id,
                              Run.date,
                              Run.name,
                              Run.duration,
                              RunHistory.version_tag,
                              RunHistory.cc_version,
                              RunHistory.description,
                              stmt.c.report_count)

            q = process_run_filter(session, q, run_filter)

            q = q.outerjoin(stmt, Run.id == stmt.c.run_id) \
                .outerjoin(tag_q, Run.id == tag_q.c.run_id) \
                .outerjoin(RunHistory,
                           RunHistory.id == tag_q.c.run_history_id) \
                .group_by(Run.id,
                          RunHistory.version_tag,
                          RunHistory.cc_version,
                          RunHistory.description,
                          stmt.c.report_count)

            q = sort_run_data_query(q, sort_mode)

            if limit:
                q = q.limit(limit).offset(offset)

            # Get the runs.
            run_data = q.all()

            # Set run ids filter by using the previous results.
            if not run_filter:
                run_filter = RunFilter()

            run_filter.ids = [r[0] for r in run_data]

            # Get report count for each detection statuses.
            status_q = session.query(Report.run_id,
                                     Report.detection_status,
                                     func.count(Report.bug_id))

            if run_filter and run_filter.ids is not None:
                status_q = status_q.filter(Report.run_id.in_(run_filter.ids))

            status_q = status_q.group_by(Report.run_id,
                                         Report.detection_status)

            status_sum = defaultdict(defaultdict)
            for run_id, status, count in status_q:
                status_sum[run_id][detection_status_enum(status)] = count

            # Get analyzer statistics.
            analyzer_statistics = defaultdict(lambda: defaultdict())
            stat_q = session.query(AnalyzerStatistic,
                                   Run.id)

            if run_filter and run_filter.ids is not None:
                stat_q = stat_q.filter(Run.id.in_(run_filter.ids))

            stat_q = stat_q \
                .outerjoin(RunHistory,
                           RunHistory.id == AnalyzerStatistic.run_history_id) \
                .outerjoin(Run,
                           Run.id == RunHistory.run_id)

            for stat, run_id in stat_q:
                analyzer_statistics[run_id][stat.analyzer_type] = \
                    ttypes.AnalyzerStatistics(failed=stat.failed,
                                              successful=stat.successful)

            results = []

            for run_id, run_date, run_name, duration, tag, cc_version, \
                description, report_count \
                    in run_data:

                if report_count is None:
                    report_count = 0

                analyzer_stats = analyzer_statistics[run_id]
                results.append(RunData(runId=run_id,
                                       runDate=str(run_date),
                                       name=run_name,
                                       duration=duration,
                                       resultCount=report_count,
                                       detectionStatusCount=status_sum[run_id],
                                       versionTag=tag,
                                       codeCheckerVersion=cc_version,
                                       analyzerStatistics=analyzer_stats,
                                       description=description))
            return results

    @exc_to_thrift_reqfail
    @timeit
    def getRunCount(self, run_filter):
        self.__require_access()

        with DBSession(self.__Session) as session:
            query = session.query(Run.id)
            query = process_run_filter(session, query, run_filter)

        return query.count()

    def getCheckCommand(self, run_history_id, run_id):
        self.__require_access()

        if not run_history_id and not run_id:
            return ""

        with DBSession(self.__Session) as session:
            query = session.query(RunHistory.check_command)

            if run_history_id:
                query = query.filter(RunHistory.id == run_history_id)
            elif run_id:
                query = query.filter(RunHistory.run_id == run_id) \
                    .order_by(RunHistory.time.desc()) \
                    .limit(1)

            history = query.first()

            if not history or not history[0]:
                return ""

        return zlib.decompress(history[0]).decode('utf-8')

    @exc_to_thrift_reqfail
    @timeit
    def getRunHistory(self, run_ids, limit, offset, run_history_filter):
        self.__require_access()

        limit = verify_limit_range(limit)

        with DBSession(self.__Session) as session:

            res = session.query(RunHistory)

            res = process_run_history_filter(res, run_ids, run_history_filter)

            res = res.order_by(RunHistory.time.desc())

            if limit:
                res = res.limit(limit).offset(offset)

            results = []
            for history in res:
                analyzer_statistics = {}
                for stat in history.analyzer_statistics:
                    analyzer_statistics[stat.analyzer_type] = \
                        ttypes.AnalyzerStatistics(
                            failed=stat.failed,
                            successful=stat.successful)

                results.append(RunHistoryData(
                    id=history.id,
                    runId=history.run.id,
                    runName=history.run.name,
                    versionTag=history.version_tag,
                    user=history.user,
                    time=str(history.time),
                    codeCheckerVersion=history.cc_version,
                    analyzerStatistics=analyzer_statistics,
                    description=history.description))

            return results

    @exc_to_thrift_reqfail
    @timeit
    def getRunHistoryCount(self, run_ids, run_history_filter):
        self.__require_access()

        with DBSession(self.__Session) as session:
            query = session.query(RunHistory.id)
            query = process_run_history_filter(query,
                                               run_ids,
                                               run_history_filter)

        return query.count()

    @exc_to_thrift_reqfail
    @timeit
    def getReport(self, reportId):
        self.__require_access()

        with DBSession(self.__Session) as session:

            result = session.query(Report,
                                   File,
                                   ReviewStatus) \
                .filter(Report.id == reportId) \
                .outerjoin(File, Report.file_id == File.id) \
                .outerjoin(ReviewStatus,
                           ReviewStatus.bug_hash == Report.bug_id) \
                .limit(1).one_or_none()

            if not result:
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE,
                    "Report " + str(reportId) + " not found!")

            report, source_file, review_status = result
            return ReportData(
                runId=report.run_id,
                bugHash=report.bug_id,
                checkedFile=source_file.filepath,
                checkerMsg=report.checker_message,
                reportId=report.id,
                fileId=source_file.id,
                line=report.line,
                column=report.column,
                checkerId=report.checker_id,
                severity=report.severity,
                reviewData=create_review_data(review_status),
                detectionStatus=detection_status_enum(report.detection_status),
                detectedAt=str(report.detected_at),
                fixedAt=str(report.fixed_at) if report.fixed_at else None)

    @exc_to_thrift_reqfail
    @timeit
    def getDiffResultsHash(self, run_ids, report_hashes, diff_type,
                           skip_detection_statuses):
        self.__require_access()

        if not skip_detection_statuses:
            skip_detection_statuses = [ttypes.DetectionStatus.RESOLVED,
                                       ttypes.DetectionStatus.OFF,
                                       ttypes.DetectionStatus.UNAVAILABLE]

        # Convert statuses to string.
        skip_statuses_str = [detection_status_str(status)
                             for status in skip_detection_statuses]

        with DBSession(self.__Session) as session:
            if diff_type == DiffType.NEW:
                # In postgresql we can select multiple rows filled with
                # constants by using `unnest` function. In sqlite we have to
                # use multiple UNION ALL.

                if not report_hashes:
                    return []

                base_hashes = session.query(Report.bug_id.label('bug_id')) \
                    .outerjoin(File, Report.file_id == File.id) \
                    .filter(Report.detection_status.notin_(skip_statuses_str))

                if run_ids:
                    base_hashes = \
                        base_hashes.filter(Report.run_id.in_(run_ids))

                if self.__product.driver_name == 'postgresql':
                    new_hashes = select([func.unnest(report_hashes)
                                         .label('bug_id')]) \
                        .except_(base_hashes).alias('new_bugs')
                    return [res[0] for res in session.query(new_hashes)]
                else:
                    # The maximum number of compound select in sqlite is 500
                    # by default. We increased SQLITE_MAX_COMPOUND_SELECT
                    # limit but when the number of compound select was larger
                    # than 8435 sqlite threw a `Segmentation fault` error.
                    # For this reason we create queries with chunks.
                    new_hashes = []
                    chunk_size = 500
                    for chunk in [report_hashes[i:i + chunk_size] for
                                  i in range(0, len(report_hashes),
                                             chunk_size)]:
                        new_hashes_query = union_all(*[
                            select([bindparam('bug_id' + str(i), h)
                                    .label('bug_id')])
                            for i, h in enumerate(chunk)])
                        q = select([new_hashes_query]).except_(base_hashes)
                        new_hashes.extend([res[0] for res in session.query(q)])

                    return new_hashes
            elif diff_type == DiffType.RESOLVED:
                results = session.query(Report.bug_id) \
                    .filter(Report.bug_id.notin_(report_hashes))

                if run_ids:
                    results = results.filter(Report.run_id.in_(run_ids))

                return [res[0] for res in results]

            elif diff_type == DiffType.UNRESOLVED:
                results = session.query(Report.bug_id) \
                    .filter(Report.bug_id.in_(report_hashes)) \
                    .filter(Report.detection_status.notin_(skip_statuses_str))

                if run_ids:
                    results = results.filter(Report.run_id.in_(run_ids))

                return [res[0] for res in results]

            else:
                return []

    @exc_to_thrift_reqfail
    @timeit
    def getRunResults(self, run_ids, limit, offset, sort_types,
                      report_filter, cmp_data, get_details):
        self.__require_access()

        limit = verify_limit_range(limit)

        with DBSession(self.__Session) as session:
            results = []

            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                sort_types, sort_type_map, order_type_map = \
                    get_sort_map(sort_types, True)

                selects = [func.max(Report.id).label('id')]
                for sort in sort_types:
                    sorttypes = sort_type_map.get(sort.type)
                    for sorttype in sorttypes:
                        if sorttype[0] != 'bug_path_length':
                            selects.append(func.max(sorttype[0])
                                           .label(sorttype[1]))

                unique_reports = session.query(*selects)
                unique_reports = apply_report_filter(unique_reports,
                                                     filter_expression)
                unique_reports = unique_reports \
                    .group_by(Report.bug_id) \
                    .subquery()

                # Sort the results
                sorted_reports = \
                    session.query(unique_reports.c.id)

                sorted_reports = sort_results_query(sorted_reports,
                                                    sort_types,
                                                    sort_type_map,
                                                    order_type_map,
                                                    True)

                sorted_reports = sorted_reports \
                    .limit(limit).offset(offset).subquery()

                q = session.query(Report.id, Report.bug_id,
                                  Report.checker_message, Report.checker_id,
                                  Report.severity, Report.detected_at,
                                  Report.fixed_at, ReviewStatus,
                                  File.filename, File.filepath,
                                  Report.path_length, Report.analyzer_name) \
                    .outerjoin(File, Report.file_id == File.id) \
                    .outerjoin(ReviewStatus,
                               ReviewStatus.bug_hash == Report.bug_id) \
                    .outerjoin(sorted_reports,
                               sorted_reports.c.id == Report.id) \
                    .filter(sorted_reports.c.id.isnot(None))

                # We have to sort the results again because an ORDER BY in a
                # subtable is broken by the JOIN.
                q = sort_results_query(q,
                                       sort_types,
                                       sort_type_map,
                                       order_type_map)

                query_result = q.all()

                # Get report details if it is required.
                report_details = {}
                if get_details:
                    report_ids = [r[0] for r in query_result]
                    report_details = get_report_details(session, report_ids)

                for report_id, bug_id, checker_msg, checker, severity, \
                    detected_at, fixed_at, status, filename, path, \
                        bug_path_len, analyzer_name in query_result:
                    review_data = create_review_data(status)

                    results.append(
                        ReportData(bugHash=bug_id,
                                   checkedFile=filename,
                                   checkerMsg=checker_msg,
                                   checkerId=checker,
                                   severity=severity,
                                   reviewData=review_data,
                                   detectedAt=str(detected_at),
                                   fixedAt=str(fixed_at),
                                   bugPathLength=bug_path_len,
                                   details=report_details.get(report_id),
                                   analyzerName=analyzer_name))
            else:
                q = session.query(Report.run_id, Report.id, Report.file_id,
                                  Report.line, Report.column,
                                  Report.detection_status, Report.bug_id,
                                  Report.checker_message, Report.checker_id,
                                  Report.severity, Report.detected_at,
                                  Report.fixed_at, ReviewStatus,
                                  File.filepath,
                                  Report.path_length, Report.analyzer_name) \
                    .outerjoin(File, Report.file_id == File.id) \
                    .outerjoin(ReviewStatus,
                               ReviewStatus.bug_hash == Report.bug_id) \
                    .filter(filter_expression)

                sort_types, sort_type_map, order_type_map = \
                    get_sort_map(sort_types)

                q = sort_results_query(q, sort_types, sort_type_map,
                                       order_type_map)

                q = q.limit(limit).offset(offset)

                query_result = q.all()

                # Get report details if it is required.
                report_details = {}
                if get_details:
                    report_ids = [r[1] for r in query_result]
                    report_details = get_report_details(session, report_ids)

                for run_id, report_id, file_id, line, column, d_status, \
                    bug_id, checker_msg, checker, severity, detected_at,\
                    fixed_at, r_status, path, bug_path_len, analyzer_name \
                        in query_result:

                    review_data = create_review_data(r_status)
                    results.append(
                        ReportData(runId=run_id,
                                   bugHash=bug_id,
                                   checkedFile=path,
                                   checkerMsg=checker_msg,
                                   reportId=report_id,
                                   fileId=file_id,
                                   line=line,
                                   column=column,
                                   checkerId=checker,
                                   severity=severity,
                                   reviewData=review_data,
                                   detectionStatus=detection_status_enum(
                                       d_status),
                                   detectedAt=str(detected_at),
                                   fixedAt=str(fixed_at) if fixed_at else None,
                                   bugPathLength=bug_path_len,
                                   details=report_details.get(report_id),
                                   analyzerName=analyzer_name))

            return results

    @timeit
    def getRunReportCounts(self, run_ids, report_filter, limit, offset):
        """
          Count the results separately for multiple runs.
          If an empty run id list is provided the report
          counts will be calculated for all of the available runs.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = []
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter)

            count_expr = create_count_expression(report_filter)
            q = session.query(Run.id,
                              Run.name,
                              count_expr) \
                .select_from(Report)

            q = q.outerjoin(File, Report.file_id == File.id) \
                .outerjoin(ReviewStatus,
                           ReviewStatus.bug_hash == Report.bug_id) \
                .outerjoin(Run,
                           Report.run_id == Run.id) \
                .filter(filter_expression) \
                .order_by(Run.name) \
                .group_by(Run.id)

            if limit:
                q = q.limit(limit).offset(offset)

            for run_id, run_name, count in q:
                report_count = RunReportCount(runId=run_id,
                                              name=run_name,
                                              reportCount=count)
                results.append(report_count)

            return results

    @exc_to_thrift_reqfail
    @timeit
    def getRunResultCount(self, run_ids, report_filter, cmp_data):
        self.__require_access()

        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            q = session.query(Report.bug_id)
            q = apply_report_filter(q, filter_expression)

            if report_filter is not None and report_filter.isUnique:
                q = q.group_by(Report.bug_id)

            report_count = q.count()
            if report_count is None:
                report_count = 0

            return report_count

    @staticmethod
    @timeit
    def __construct_bug_item_list(session, report_id, item_type):

        q = session.query(item_type) \
            .filter(item_type.report_id == report_id) \
            .order_by(item_type.order)

        bug_items = []

        for event in q:
            f = session.query(File).get(event.file_id)
            bug_items.append((event, f.filepath))

        return bug_items

    @exc_to_thrift_reqfail
    @timeit
    def getReportDetails(self, reportId):
        """
        Parameters:
         - reportId
        """
        self.__require_access()
        with DBSession(self.__Session) as session:
            return get_report_details(session, [reportId])[reportId]

    def _setReviewStatus(self, report_id, status, message, session):
        """
        This function sets the review status of the given report. This is the
        implementation of changeReviewStatus(), but it is also extended with
        a session parameter which represents a database transaction. This is
        needed because during storage a specific session object has to be used.
        """
        report = session.query(Report).get(report_id)
        if report:
            review_status = session.query(ReviewStatus).get(report.bug_id)
            if review_status is None:
                review_status = ReviewStatus()
                review_status.bug_hash = report.bug_id

            user = self.__get_username()

            old_status = review_status.status if review_status.status \
                else review_status_str(ttypes.ReviewStatus.UNREVIEWED)
            old_msg = review_status.message.decode('utf-8') \
                if review_status.message else None

            review_status.status = review_status_str(status)
            review_status.author = user
            review_status.message = message.encode('utf8') if message else b''
            review_status.date = datetime.now()
            session.add(review_status)

            # Create a system comment if the review status or the message is
            # changed.
            if old_status != review_status.status or old_msg != message:
                old_review_status = escape_whitespaces(old_status.capitalize())
                new_review_status = \
                    escape_whitespaces(review_status.status.capitalize())
                if message:
                    system_comment_msg = \
                        'rev_st_changed_msg {0} {1} {2}'.format(
                            old_review_status, new_review_status,
                            escape_whitespaces(message))
                else:
                    system_comment_msg = 'rev_st_changed {0} {1}'.format(
                        old_review_status, new_review_status)

                system_comment = self.__add_comment(review_status.bug_hash,
                                                    system_comment_msg,
                                                    CommentKindValue.SYSTEM)
                session.add(system_comment)

            session.flush()

            return True
        else:
            msg = "No report found in the database."
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def isReviewStatusChangeDisabled(self):
        """
        Return True if review status change is disabled.
        """
        with DBSession(self.__config_database) as session:
            product = session.query(Product).get(self.__product.id)
            return product.is_review_status_change_disabled

    @exc_to_thrift_reqfail
    @timeit
    def changeReviewStatus(self, report_id, status, message):
        """
        Change review status of the bug by report id.
        """
        self.__require_permission([permissions.PRODUCT_ACCESS,
                                   permissions.PRODUCT_STORE])

        if self.isReviewStatusChangeDisabled():
            msg = "Review status change is disabled!"
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.GENERAL, msg)

        with DBSession(self.__Session) as session:
            res = self._setReviewStatus(report_id, status, message, session)
            session.commit()

            LOG.info("Review status of report '%s' was changed to '%s' by %s.",
                     report_id, review_status_str(status),
                     self.__get_username())

        return res

    @exc_to_thrift_reqfail
    @timeit
    def getComments(self, report_id):
        """
            Return the list of comments for the given bug.
        """
        self.__require_access()

        with DBSession(self.__Session) as session:
            report = session.query(Report).get(report_id)
            if report:
                result = []

                comments = session.query(Comment) \
                    .filter(Comment.bug_hash == report.bug_id) \
                    .order_by(Comment.created_at.desc()) \
                    .all()

                context = webserver_context.get_context()
                for comment in comments:
                    message = comment.message.decode('utf-8')
                    sys_comment = comment_kind_from_thrift_type(
                        ttypes.CommentKind.SYSTEM)
                    if comment.kind == sys_comment:
                        elements = shlex.split(message)
                        system_comment = context.system_comment_map.get(
                            elements[0])
                        if system_comment:
                            for idx, value in enumerate(elements[1:]):
                                system_comment = system_comment.replace(
                                    '{' + str(idx) + '}', value)
                            message = system_comment

                    result.append(CommentData(
                        comment.id,
                        comment.author,
                        message,
                        str(comment.created_at),
                        comment_kind_to_thrift_type(comment.kind)))

                return result
            else:
                msg = 'Report id ' + str(report_id) + \
                      ' was not found in the database.'
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def getCommentCount(self, report_id):
        """
            Return the number of comments for the given bug.
        """
        self.__require_access()
        with DBSession(self.__Session) as session:
            report = session.query(Report).get(report_id)
            if report:
                commentCount = session.query(Comment) \
                    .filter(Comment.bug_hash == report.bug_id) \
                    .count()

            if commentCount is None:
                commentCount = 0

            return commentCount

    @exc_to_thrift_reqfail
    @timeit
    def addComment(self, report_id, comment_data):
        """ Add new comment for the given bug. """
        self.__require_access()

        if not comment_data.message.strip():
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.GENERAL,
                'The comment message can not be empty!')

        with DBSession(self.__Session) as session:
            report = session.query(Report).get(report_id)
            if report:
                comment = self.__add_comment(report.bug_id,
                                             comment_data.message)
                session.add(comment)
                session.commit()

                return True
            else:
                msg = 'Report id ' + str(report_id) + \
                      ' was not found in the database.'
                LOG.error(msg)
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def updateComment(self, comment_id, content):
        """
            Update the given comment message with new content. We allow
            comments to be updated by it's original author only, except for
            Anyonymous comments that can be updated by anybody.
        """
        self.__require_access()

        if not content.strip():
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.GENERAL,
                'The comment message can not be empty!')

        with DBSession(self.__Session) as session:

            user = self.__get_username()

            comment = session.query(Comment).get(comment_id)
            if comment:
                if comment.author != 'Anonymous' and comment.author != user:
                    raise codechecker_api_shared.ttypes.RequestFailed(
                        codechecker_api_shared.ttypes.ErrorCode.UNAUTHORIZED,
                        'Unathorized comment modification!')

                # Create system comment if the message is changed.
                message = comment.message.decode('utf-8')
                if message != content:
                    system_comment_msg = 'comment_changed {0} {1}'.format(
                        escape_whitespaces(message),
                        escape_whitespaces(content))

                    system_comment = \
                        self.__add_comment(comment.bug_hash,
                                           system_comment_msg,
                                           CommentKindValue.SYSTEM)
                    session.add(system_comment)

                comment.message = content.encode('utf-8')
                session.add(comment)

                session.commit()
                return True
            else:
                msg = 'Comment id ' + str(comment_id) + \
                      ' was not found in the database.'
                LOG.error(msg)
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def removeComment(self, comment_id):
        """
            Remove the comment. We allow comments to be removed by it's
            original author only, except for Anyonymous comments that can be
            updated by anybody.
        """
        self.__require_access()

        user = self.__get_username()

        with DBSession(self.__Session) as session:

            comment = session.query(Comment).get(comment_id)
            if comment:
                if comment.author != 'Anonymous' and comment.author != user:
                    raise codechecker_api_shared.ttypes.RequestFailed(
                        codechecker_api_shared.ttypes.ErrorCode.UNAUTHORIZED,
                        'Unathorized comment modification!')
                session.delete(comment)
                session.commit()

                LOG.info("Comment '%s...' was removed from bug hash '%s' by "
                         "'%s'.", comment.message[:10], comment.bug_hash,
                         self.__get_username())

                return True
            else:
                msg = 'Comment id ' + str(comment_id) + \
                      ' was not found in the database.'
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def getCheckerDoc(self, checkerId):
        """
        Parameters:
         - checkerId
        """

        missing_doc = "No documentation found for checker: " + checkerId + \
                      "\n\nPlease refer to the documentation at the "

        if "." in checkerId:
            sa_link = "http://clang-analyzer.llvm.org/available_checks.html"
            missing_doc += "[ClangSA](" + sa_link + ")"
        elif "-" in checkerId:
            tidy_link = "http://clang.llvm.org/extra/clang-tidy/checks/" + \
                      checkerId + ".html"
            missing_doc += "[ClangTidy](" + tidy_link + ")"
        missing_doc += " homepage."

        try:
            md_file = self.__checker_doc_map.get(checkerId)
            if md_file:
                md_file = os.path.join(self.__checker_md_docs, md_file)
                try:
                    with open(md_file, 'r',
                              encoding='utf-8',
                              errors='ignore') as md_content:
                        missing_doc = md_content.read()
                except (IOError, OSError):
                    LOG.warning("Failed to read checker documentation: %s",
                                md_file)

            return missing_doc

        except Exception as ex:
            msg = str(ex)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.IOERROR, msg)

    @exc_to_thrift_reqfail
    @timeit
    def getSourceFileData(self, fileId, fileContent, encoding):
        """
        Parameters:
         - fileId
         - fileContent
         - enum Encoding
        """
        self.__require_access()
        with DBSession(self.__Session) as session:
            sourcefile = session.query(File).get(fileId)

            if sourcefile is None:
                return SourceFileData()

            if fileContent:
                cont = session.query(FileContent).get(sourcefile.content_hash)
                source = zlib.decompress(cont.content)

                if encoding == Encoding.BASE64:
                    source = base64.b64encode(source)

                source = source.decode('utf-8', errors='ignore')
                return SourceFileData(fileId=sourcefile.id,
                                      filePath=sourcefile.filepath,
                                      fileContent=source)
            else:
                return SourceFileData(fileId=sourcefile.id,
                                      filePath=sourcefile.filepath)

    @exc_to_thrift_reqfail
    @timeit
    def getLinesInSourceFileContents(self, lines_in_files_requested, encoding):
        self.__require_access()
        with DBSession(self.__Session) as session:
            res = defaultdict(lambda: defaultdict(str))
            for lines_in_file in lines_in_files_requested:
                if lines_in_file.fileId is None:
                    LOG.warning("File content requested without a fileId.")
                    LOG.warning(lines_in_file)
                    continue
                sourcefile = session.query(File).get(lines_in_file.fileId)
                cont = session.query(FileContent).get(sourcefile.content_hash)
                lines = zlib.decompress(
                    cont.content).decode('utf-8', 'ignore').split('\n')
                for line in lines_in_file.lines:
                    content = '' if len(lines) < line else lines[line - 1]
                    if encoding == Encoding.BASE64:
                        content = convert.to_b64(content)
                    res[lines_in_file.fileId][line] = content
            return res

    @exc_to_thrift_reqfail
    @timeit
    def getCheckerCounts(self, run_ids, report_filter, cmp_data, limit,
                         offset):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = []
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                q = session.query(func.max(Report.checker_id).label(
                                      'checker_id'),
                                  func.max(Report.severity).label(
                                      'severity'),
                                  Report.bug_id)
            else:
                q = session.query(Report.checker_id,
                                  Report.severity,
                                  func.count(Report.id))

            q = apply_report_filter(q, filter_expression)

            if is_unique:
                q = q.group_by(Report.bug_id).subquery()
                unique_checker_q = session.query(q.c.checker_id,
                                                 func.max(q.c.severity),
                                                 func.count(q.c.bug_id)) \
                    .group_by(q.c.checker_id) \
                    .order_by(q.c.checker_id)
            else:
                unique_checker_q = q.group_by(Report.checker_id,
                                              Report.severity) \
                    .order_by(Report.checker_id)

            if limit:
                unique_checker_q = unique_checker_q.limit(limit).offset(offset)

            for name, severity, count in unique_checker_q:
                checker_count = CheckerCount(name=name,
                                             severity=severity,
                                             count=count)
                results.append(checker_count)
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getAnalyzerNameCounts(self, run_ids, report_filter, cmp_data, limit,
                              offset):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = {}
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                q = session.query(func.max(Report.analyzer_name).label(
                                      'analyzer_name'),
                                  Report.bug_id)
            else:
                q = session.query(Report.analyzer_name,
                                  func.count(Report.id))

            q = apply_report_filter(q, filter_expression)

            if is_unique:
                q = q.group_by(Report.bug_id).subquery()
                analyzer_name_q = session.query(q.c.analyzer_name,
                                                func.count(q.c.bug_id)) \
                    .group_by(q.c.analyzer_name) \
                    .order_by(q.c.analyzer_name)
            else:
                analyzer_name_q = q.group_by(Report.analyzer_name) \
                    .order_by(Report.analyzer_name)

            if limit:
                analyzer_name_q = analyzer_name_q.limit(limit).offset(offset)

            for name, count in analyzer_name_q:
                results[name] = count

        return results

    @exc_to_thrift_reqfail
    @timeit
    def getSeverityCounts(self, run_ids, report_filter, cmp_data):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()
        results = {}
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                q = session.query(func.max(Report.severity).label('severity'),
                                  Report.bug_id)
            else:
                q = session.query(Report.severity,
                                  func.count(Report.id))

            q = apply_report_filter(q, filter_expression)

            if is_unique:
                q = q.group_by(Report.bug_id).subquery()
                severities = session.query(q.c.severity,
                                           func.count(q.c.bug_id)) \
                    .group_by(q.c.severity)
            else:
                severities = q.group_by(Report.severity)

            results = dict(severities)
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getCheckerMsgCounts(self, run_ids, report_filter, cmp_data, limit,
                            offset):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = {}
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                q = session.query(func.max(Report.checker_message).label(
                                      'checker_message'),
                                  Report.bug_id)
            else:
                q = session.query(Report.checker_message,
                                  func.count(Report.id))

            q = apply_report_filter(q, filter_expression)

            if is_unique:
                q = q.group_by(Report.bug_id).subquery()
                checker_messages = session.query(q.c.checker_message,
                                                 func.count(q.c.bug_id)) \
                    .group_by(q.c.checker_message) \
                    .order_by(q.c.checker_message)
            else:
                checker_messages = q.group_by(Report.checker_message) \
                                    .order_by(Report.checker_message)

            if limit:
                checker_messages = checker_messages.limit(limit).offset(offset)

            results = dict(checker_messages.all())
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getReviewStatusCounts(self, run_ids, report_filter, cmp_data):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()
        results = defaultdict(int)
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            is_unique = report_filter is not None and report_filter.isUnique
            if is_unique:
                q = session.query(Report.bug_id,
                                  func.max(ReviewStatus.status).label(
                                      'status'))
            else:
                q = session.query(func.max(Report.bug_id),
                                  ReviewStatus.status,
                                  func.count(Report.id))

            q = apply_report_filter(q, filter_expression)

            if is_unique:
                q = q.group_by(Report.bug_id).subquery()
                review_statuses = session.query(func.max(q.c.bug_id),
                                                q.c.status,
                                                func.count(q.c.bug_id)) \
                    .group_by(q.c.status)
            else:
                review_statuses = q.group_by(ReviewStatus.status)

            for _, rev_status, count in review_statuses:
                if rev_status is None:
                    # If no review status is set count it as unreviewed.
                    rev_status = ttypes.ReviewStatus.UNREVIEWED
                    results[rev_status] += count
                else:
                    rev_status = review_status_enum(rev_status)
                    results[rev_status] += count
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getFileCounts(self, run_ids, report_filter, cmp_data, limit, offset):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = {}
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            stmt = session.query(Report.bug_id,
                                 Report.file_id)
            stmt = apply_report_filter(stmt, filter_expression)

            if report_filter is not None and report_filter.isUnique:
                stmt = stmt.group_by(Report.bug_id, Report.file_id)

            stmt = stmt.subquery()

            # When using pg8000, 1 cannot be passed as parameter to the count
            # function. This is the reason why we have to convert it to
            # Integer (see: https://github.com/mfenniak/pg8000/issues/110)
            count_int = cast(1, sqlalchemy.Integer)
            report_count = session.query(stmt.c.file_id,
                                         func.count(count_int).label(
                                             'report_count')) \
                .group_by(stmt.c.file_id)

            if limit:
                report_count = report_count.limit(limit).offset(offset)

            report_count = report_count.subquery()
            file_paths = session.query(File.filepath,
                                       report_count.c.report_count) \
                .join(report_count,
                      report_count.c.file_id == File.id)

            for fp, count in file_paths:
                results[fp] = count
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getRunHistoryTagCounts(self, run_ids, report_filter, cmp_data, limit,
                               offset):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()

        limit = verify_limit_range(limit)

        results = []
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            tag_run_ids = session.query(RunHistory.run_id.distinct()) \
                .filter(RunHistory.version_tag.isnot(None)) \

            if run_ids:
                tag_run_ids = tag_run_ids.filter(
                    RunHistory.run_id.in_(run_ids))

            tag_run_ids = tag_run_ids.subquery()

            report_cnt_q = session.query(Report.run_id,
                                         Report.bug_id,
                                         Report.detected_at,
                                         Report.fixed_at) \
                .outerjoin(File, Report.file_id == File.id) \
                .outerjoin(ReviewStatus,
                           ReviewStatus.bug_hash == Report.bug_id) \
                .filter(filter_expression) \
                .filter(Report.run_id.in_(tag_run_ids)) \
                .subquery()

            is_unique = report_filter is not None and report_filter.isUnique
            count_expr = func.count(report_cnt_q.c.bug_id if not is_unique
                                    else report_cnt_q.c.bug_id.distinct())

            count_q = session.query(RunHistory.id.label('run_history_id'),
                                    count_expr.label('report_count')) \
                .outerjoin(report_cnt_q,
                           report_cnt_q.c.run_id == RunHistory.run_id) \
                .filter(RunHistory.version_tag.isnot(None)) \
                .filter(get_open_reports_date_filter_query(report_cnt_q.c)) \
                .group_by(RunHistory.id) \
                .subquery()

            tag_q = session.query(RunHistory.run_id.label('run_id'),
                                  RunHistory.id.label('run_history_id')) \
                .filter(RunHistory.version_tag.isnot(None))

            if run_ids:
                tag_q = tag_q.filter(RunHistory.run_id.in_(run_ids))

            if report_filter and report_filter.runTag is not None:
                tag_q = tag_q.filter(RunHistory.id.in_(report_filter.runTag))

            tag_q = tag_q.subquery()

            q = session.query(tag_q.c.run_history_id,
                              func.max(Run.id),
                              func.max(Run.name).label('run_name'),
                              func.max(RunHistory.id),
                              func.max(RunHistory.time),
                              func.max(RunHistory.version_tag),
                              func.max(count_q.c.report_count)) \
                .outerjoin(RunHistory,
                           RunHistory.id == tag_q.c.run_history_id) \
                .outerjoin(Run, Run.id == tag_q.c.run_id) \
                .outerjoin(count_q,
                           count_q.c.run_history_id == RunHistory.id) \
                .filter(RunHistory.version_tag.isnot(None)) \
                .group_by(tag_q.c.run_history_id, RunHistory.time) \
                .order_by(RunHistory.time.desc())

            if limit:
                q = q.limit(limit).offset(offset)

            for _, run_id, run_name, tag_id, version_time, tag, count in q:
                if tag:
                    results.append(RunTagCount(id=tag_id,
                                               time=str(version_time),
                                               name=tag,
                                               runName=run_name,
                                               runId=run_id,
                                               count=count if count else 0))
        return results

    @exc_to_thrift_reqfail
    @timeit
    def getDetectionStatusCounts(self, run_ids, report_filter, cmp_data):
        """
          If the run id list is empty the metrics will be counted
          for all of the runs and in compare mode all of the runs
          will be used as a baseline excluding the runs in compare data.
        """
        self.__require_access()
        results = {}
        with DBSession(self.__Session) as session:
            filter_expression = process_report_filter(session, run_ids,
                                                      report_filter, cmp_data)

            count_expr = func.count(literal_column('*'))

            q = session.query(Report.detection_status,
                              count_expr)

            q = apply_report_filter(q, filter_expression)

            detection_stats = q.group_by(Report.detection_status).all()

            results = dict(detection_stats)
            results = {
                detection_status_enum(k): v for k,
                v in results.items()}

        return results

    # -----------------------------------------------------------------------
    @timeit
    def getPackageVersion(self):
        return self.__package_version

    # -----------------------------------------------------------------------
    @exc_to_thrift_reqfail
    @timeit
    def removeRunResults(self, run_ids):
        self.__require_store()

        failed = False
        for run_id in run_ids:
            try:
                self.removeRun(run_id, None)
            except Exception as ex:
                LOG.error("Failed to remove run: %s", run_id)
                LOG.error(ex)
                failed = True
        return not failed

    def __removeReports(self, session, report_ids, chunk_size=500):
        """
        Removing reports in chunks.
        """
        for r_ids in [report_ids[i:i + chunk_size] for
                      i in range(0, len(report_ids),
                                 chunk_size)]:
            session.query(Report) \
                .filter(Report.id.in_(r_ids)) \
                .delete(synchronize_session=False)

    @exc_to_thrift_reqfail
    @timeit
    def removeRunReports(self, run_ids, report_filter, cmp_data):
        self.__require_store()

        if not run_ids:
            run_ids = []

        if cmp_data and cmp_data.runIds:
            run_ids.extend(cmp_data.runIds)

        with DBSession(self.__Session) as session:
            check_remove_runs_lock(session, run_ids)

            try:
                filter_expression = process_report_filter(session, run_ids,
                                                          report_filter,
                                                          cmp_data)

                q = session.query(Report.id) \
                    .outerjoin(File, Report.file_id == File.id) \
                    .outerjoin(ReviewStatus,
                               ReviewStatus.bug_hash == Report.bug_id) \
                    .filter(filter_expression)

                reports_to_delete = [r[0] for r in q]
                if reports_to_delete:
                    self.__removeReports(session, reports_to_delete)

                session.commit()
                session.close()

                LOG.info("The following reports were removed by '%s': %s",
                         self.__get_username(), reports_to_delete)
            except Exception as ex:
                session.rollback()
                LOG.error("Database cleanup failed.")
                LOG.error(ex)
                return False

        # Delete files and contents that are not present
        # in any bug paths.
        db_cleanup.remove_unused_files(self.__Session)

        return True

    @exc_to_thrift_reqfail
    @timeit
    def removeRun(self, run_id, run_filter):
        self.__require_store()

        # Remove the whole run.
        with DBSession(self.__Session) as session:
            check_remove_runs_lock(session, [run_id])

            if not run_filter:
                run_filter = RunFilter(ids=[run_id])

            q = session.query(Run)
            q = process_run_filter(session, q, run_filter)
            q.delete(synchronize_session=False)

            session.commit()
            session.close()

            runs = run_filter.names if run_filter.names else run_filter.ids
            LOG.info("Runs '%s' were removed by '%s'.", runs,
                     self.__get_username())

        # Delete files and contents that are not present
        # in any bug paths.
        db_cleanup.remove_unused_files(self.__Session)

        return True

    @exc_to_thrift_reqfail
    @timeit
    def updateRunData(self, run_id, new_run_name):
        self.__require_store()

        if not new_run_name:
            msg = 'No new run name was given to update the run.'
            LOG.error(msg)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.GENERAL, msg)

        with DBSession(self.__Session) as session:
            check_new_run_name = session.query(Run) \
                    .filter(Run.name == new_run_name) \
                    .all()
            if check_new_run_name:
                msg = "New run name '" + new_run_name + "' already exists."
                LOG.error(msg)

                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

            run_data = session.query(Run).get(run_id)
            if run_data:
                old_run_name = run_data.name
                run_data.name = new_run_name
                session.add(run_data)
                session.commit()

                LOG.info("Run name '%s' (%d) was changed to %s by '%s'.",
                         old_run_name, run_id, new_run_name,
                         self.__get_username())

                return True
            else:
                msg = 'Run id ' + str(run_id) + \
                      ' was not found in the database.'
                LOG.error(msg)
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

        return True

    @exc_to_thrift_reqfail
    def getSuppressFile(self):
        """
        DEPRECATED the server is not started with a suppress file anymore.
        Returning empty string.
        """
        self.__require_access()
        return ''

    @exc_to_thrift_reqfail
    @timeit
    def addSourceComponent(self, name, value, description):
        """
        Adds a new source if it does not exist or updates an old one.
        """
        self.__require_admin()
        with DBSession(self.__Session) as session:
            component = session.query(SourceComponent).get(name)
            user = self.__auth_session.user if self.__auth_session else None

            if component:
                component.value = value.encode('utf-8')
                component.description = description
                component.user = user
            else:
                component = SourceComponent(name,
                                            value.encode('utf-8'),
                                            description,
                                            user)

            session.add(component)
            session.commit()

            return True

    @exc_to_thrift_reqfail
    @timeit
    def getSourceComponents(self, component_filter):
        """
        Returns the available source components.
        """
        self.__require_access()
        with DBSession(self.__Session) as session:
            q = session.query(SourceComponent)

            if component_filter and component_filter:
                sql_component_filter = [SourceComponent.name.ilike(conv(cf))
                                        for cf in component_filter]
                q = q.filter(*sql_component_filter)

            q = q.order_by(SourceComponent.name)

            return list([SourceComponentData(c.name,
                                             c.value.decode('utf-8'),
                                             c.description) for c in q])

    @exc_to_thrift_reqfail
    @timeit
    def removeSourceComponent(self, name):
        """
        Removes a source component.
        """
        self.__require_admin()

        with DBSession(self.__Session) as session:
            component = session.query(SourceComponent).get(name)
            if component:
                session.delete(component)
                session.commit()
                LOG.info("Source component '%s' has been removed by '%s'",
                         name, self.__get_username())
                return True
            else:
                msg = 'Source component ' + str(name) + \
                      ' was not found in the database.'
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.DATABASE, msg)

    @exc_to_thrift_reqfail
    @timeit
    def getMissingContentHashes(self, file_hashes):
        self.__require_store()

        if not file_hashes:
            return []

        with DBSession(self.__Session) as session:

            q = session.query(FileContent) \
                .options(sqlalchemy.orm.load_only('content_hash')) \
                .filter(FileContent.content_hash.in_(file_hashes))

            return list(set(file_hashes) -
                        set([fc.content_hash for fc in q]))

    def __store_source_files(self, source_root, filename_to_hash,
                             trim_path_prefixes):
        """
        Storing file contents from plist.
        """

        file_path_to_id = {}

        for file_name, file_hash in filename_to_hash.items():
            source_file_name = os.path.join(source_root,
                                            file_name.strip("/"))
            source_file_name = os.path.realpath(source_file_name)
            LOG.debug("Storing source file: %s", source_file_name)
            trimmed_file_path = util.trim_path_prefixes(file_name,
                                                        trim_path_prefixes)

            if not os.path.isfile(source_file_name):
                # The file was not in the ZIP file, because we already
                # have the content. Let's check if we already have a file
                # record in the database or we need to add one.

                LOG.debug(file_name + ' not found or already stored.')
                with DBSession(self.__Session) as session:
                    fid = store_handler.addFileRecord(session,
                                                      trimmed_file_path,
                                                      file_hash)
                if not fid:
                    LOG.error("File ID for %s is not found in the DB with "
                              "content hash %s. Missing from ZIP?",
                              source_file_name, file_hash)
                file_path_to_id[file_name] = fid
                LOG.debug("%d fileid found", fid)
                continue

            with DBSession(self.__Session) as session:
                file_path_to_id[file_name] = \
                    store_handler.addFileContent(session,
                                                 trimmed_file_path,
                                                 source_file_name,
                                                 file_hash,
                                                 None)

        return file_path_to_id

    def __store_reports(self, session, report_dir, source_root, run_id,
                        file_path_to_id, run_history_time, severity_map,
                        wrong_src_code_comments, skip_handler,
                        checkers, trim_path_prefixes):
        """
        Parse up and store the plist report files.
        """

        all_reports = session.query(Report) \
            .filter(Report.run_id == run_id) \
            .all()

        hash_map_reports = defaultdict(list)
        for report in all_reports:
            hash_map_reports[report.bug_id].append(report)

        already_added = set()
        new_bug_hashes = set()

        # Get checker names which was enabled during the analysis.
        enabled_checkers = set()
        disabled_checkers = set()
        checker_to_analyzer = dict()
        for analyzer_name, analyzer_checkers in checkers.items():
            if isinstance(analyzer_checkers, dict):
                for checker_name, enabled in analyzer_checkers.items():
                    checker_to_analyzer[checker_name] = analyzer_name
                    if enabled:
                        enabled_checkers.add(checker_name)
                    else:
                        disabled_checkers.add(checker_name)
            else:
                enabled_checkers.update(analyzer_checkers)

                for checker_name in analyzer_checkers:
                    checker_to_analyzer[checker_name] = analyzer_name

        def checker_is_unavailable(checker_name):
            """
            Returns True if the given checker is unavailable.

            We filter out checkers which start with 'clang-diagnostic-' because
            these are warnings and the warning list is not available right now.

            FIXME: using the 'diagtool' could be a solution later so the
            client can send the warning list to the server.
            """
            return not checker_name.startswith('clang-diagnostic-') and \
                enabled_checkers and checker_name not in enabled_checkers

        def get_analyzer_name(report):
            """ Get analyzer name for the given report. """
            analyzer_name = checker_to_analyzer.get(report.check_name)
            if analyzer_name:
                return analyzer_name

            if report.metadata:
                return report.metadata.get("analyzer", {}).get("name")

            if report.check_name.startswith('clang-diagnostic-'):
                return 'clang-tidy'

        # Processing PList files.
        _, _, report_files = next(os.walk(report_dir), ([], [], []))
        all_report_checkers = set()
        for f in report_files:
            if not f.endswith('.plist'):
                continue

            LOG.debug("Parsing input file '%s'", f)

            try:
                files, reports = plist_parser.parse_plist_file(
                    os.path.join(report_dir, f), None)
            except Exception as ex:
                LOG.error('Parsing the plist failed: %s', str(ex))
                continue

            file_ids = {}
            if reports:
                missing_ids_for_files = []

                for file_name in files.values():

                    file_name = util.trim_path_prefixes(file_name,
                                                        trim_path_prefixes)
                    file_id = file_path_to_id.get(file_name, -1)
                    if file_id == -1:
                        missing_ids_for_files.append(file_name)
                        continue

                    file_ids[file_name] = file_id

                if missing_ids_for_files:
                    LOG.error("Failed to get file path id for '%s'!",
                              file_name)
                    continue

            # Store report.
            for report in reports:
                checker_name = report.main['check_name']
                all_report_checkers.add(checker_name)

                source_file = util.trim_path_prefixes(
                    report.main['location']['file'], trim_path_prefixes)

                if skip_handler.should_skip(source_file):
                    continue
                bug_paths, bug_events, bug_extended_data = \
                    store_handler.collect_paths_events(report, file_ids,
                                                       files)
                report_path_hash = get_report_path_hash(report)
                if report_path_hash in already_added:
                    LOG.debug('Not storing report. Already added')
                    LOG.debug(report)
                    continue

                LOG.debug("Storing check results to the database.")

                LOG.debug("Storing report")
                bug_id = report.main[
                    'issue_hash_content_of_line_in_context']

                detection_status = 'new'
                detected_at = run_history_time

                if bug_id in hash_map_reports:
                    old_report = hash_map_reports[bug_id][0]
                    old_status = old_report.detection_status
                    detection_status = 'reopened' \
                        if old_status == 'resolved' else 'unresolved'
                    detected_at = old_report.detected_at

                analyzer_name = get_analyzer_name(report)
                report_id = store_handler.addReport(
                    session,
                    run_id,
                    file_ids[source_file],
                    report.main,
                    bug_paths,
                    bug_events,
                    bug_extended_data,
                    detection_status,
                    detected_at,
                    severity_map,
                    analyzer_name)

                new_bug_hashes.add(bug_id)
                already_added.add(report_path_hash)

                last_report_event = report.bug_path[-1]
                file_name = files[last_report_event['location']['file']]
                source_file_name = os.path.realpath(
                    os.path.join(source_root, file_name.strip("/")))

                if os.path.isfile(source_file_name):
                    report_line = last_report_event['location']['line']
                    source_file = os.path.basename(file_name)
                    src_comment_data = \
                        parse_codechecker_review_comment(source_file_name,
                                                         report_line,
                                                         checker_name)
                    if len(src_comment_data) == 1:
                        status = src_comment_data[0]['status']
                        rw_status = ttypes.ReviewStatus.FALSE_POSITIVE
                        if status == 'confirmed':
                            rw_status = ttypes.ReviewStatus.CONFIRMED
                        elif status == 'intentional':
                            rw_status = ttypes.ReviewStatus.INTENTIONAL

                        self._setReviewStatus(report_id,
                                              rw_status,
                                              src_comment_data[0]['message'],
                                              session)
                    elif len(src_comment_data) > 1:
                        LOG.warning(
                            "Multiple source code comment can be found "
                            "for '%s' checker in '%s' at line %s. "
                            "This bug will not be suppressed!",
                            checker_name, source_file, report_line)

                        wrong_src_code = "{0}|{1}|{2}".format(source_file,
                                                              report_line,
                                                              checker_name)
                        wrong_src_code_comments.append(wrong_src_code)

                LOG.debug("Storing done for report %d", report_id)

        # If a checker was found in a plist file it can not be disabled so we
        # will remove these checkers from the disabled checkers list and add
        # these to the enabled checkers list.
        disabled_checkers -= all_report_checkers
        enabled_checkers |= all_report_checkers

        reports_to_delete = set()
        for bug_hash, reports in hash_map_reports.items():
            if bug_hash in new_bug_hashes:
                reports_to_delete.update([x.id for x in reports])
            else:
                for report in reports:
                    # We set the fix date of a report only if the report
                    # has not been fixed before.
                    if report.fixed_at:
                        continue

                    checker = report.checker_id
                    if checker in disabled_checkers:
                        report.detection_status = 'off'
                    elif checker_is_unavailable(checker):
                        report.detection_status = 'unavailable'
                    else:
                        report.detection_status = 'resolved'

                    report.fixed_at = run_history_time

        if reports_to_delete:
            self.__removeReports(session, list(reports_to_delete))

    @staticmethod
    @exc_to_thrift_reqfail
    def __store_run_lock(session, name, username):
        """
        Store a RunLock record for the given run name into the database.
        """
        try:
            # If the run can be stored, we need to lock it first. If there is
            # already a lock in the database for the given run name which is
            # expired and multiple processes are trying to get this entry from
            # the database for update we may get the following exception:
            # could not obtain lock on row in relation "run_locks"
            # This is the reason why we have to wrap this query to a try/except
            # block.
            run_lock = session.query(RunLock) \
                .filter(RunLock.name == name) \
                .with_for_update(nowait=True).one_or_none()
        except (sqlalchemy.exc.OperationalError,
                sqlalchemy.exc.ProgrammingError) as ex:
            LOG.error("Failed to get run lock for '%s': %s", name, ex)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.DATABASE,
                "Someone is already storing to the same run. Please wait "
                "while the other storage is finished and try it again.")

        if not run_lock:
            # If there is no lock record for the given run name, the run
            # is not locked -- create a new lock.
            run_lock = RunLock(name, username)
            session.add(run_lock)
        elif run_lock.has_expired(
                db_cleanup.RUN_LOCK_TIMEOUT_IN_DATABASE):
            # There can be a lock in the database, which has already
            # expired. In this case, we assume that the previous operation
            # has failed, and thus, we can re-use the already present lock.
            run_lock.touch()
            run_lock.username = username
        else:
            # In case the lock exists and it has not expired, we must
            # consider the run a locked one.
            when = run_lock.when_expires(
                db_cleanup.RUN_LOCK_TIMEOUT_IN_DATABASE)

            username = run_lock.username if run_lock.username is not None \
                else "another user"

            LOG.info("Refusing to store into run '%s' as it is locked by "
                     "%s. Lock will expire at '%s'.", name, username, when)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.DATABASE,
                "The run named '{0}' is being stored into by {1}. If the "
                "other store operation has failed, this lock will expire "
                "at '{2}'.".format(name, username, when))

        # At any rate, if the lock has been created or updated, commit it
        # into the database.
        try:
            session.commit()
        except (sqlalchemy.exc.IntegrityError,
                sqlalchemy.orm.exc.StaleDataError):
            # The commit of this lock can fail.
            #
            # In case two store ops attempt to lock the same run name at the
            # same time, committing the lock in the transaction that commits
            # later will result in an IntegrityError due to the primary key
            # constraint.
            #
            # In case two store ops attempt to lock the same run name with
            # reuse and one of the operation hangs long enough before COMMIT
            # so that the other operation commits and thus removes the lock
            # record, StaleDataError is raised. In this case, also consider
            # the run locked, as the data changed while the transaction was
            # waiting, as another run wholly completed.

            LOG.info("Run '%s' got locked while current transaction "
                     "tried to acquire a lock. Considering run as locked.",
                     name)
            raise codechecker_api_shared.ttypes.RequestFailed(
                codechecker_api_shared.ttypes.ErrorCode.DATABASE,
                "The run named '{0}' is being stored into by another "
                "user.".format(name))

    @staticmethod
    @exc_to_thrift_reqfail
    def __free_run_lock(session, name):
        """
        Remove the lock from the database for the given run name.
        """
        # Using with_for_update() here so the database (in case it supports
        # this operation) locks the lock record's row from any other access.
        run_lock = session.query(RunLock) \
            .filter(RunLock.name == name) \
            .with_for_update(nowait=True).one()
        session.delete(run_lock)
        session.commit()

    def __check_run_limit(self, run_name):
        """
        Checks the maximum allowed of uploadable runs for the current product.
        """
        max_run_count = self.__manager.get_max_run_count()

        with DBSession(self.__config_database) as session:
            product = session.query(Product).get(self.__product.id)
            if product.run_limit:
                max_run_count = product.run_limit

        # Session that handles constraints on the run.
        with DBSession(self.__Session) as session:
            if max_run_count:
                LOG.debug("Check the maximum number of allowed "
                          "runs which is %d", max_run_count)

                run = session.query(Run) \
                    .filter(Run.name == run_name) \
                    .one_or_none()

                # If max_run_count is not set in the config file, it will allow
                # the user to upload unlimited runs.

                run_count = session.query(Run.id).count()

                # If we are not updating a run or the run count is reached the
                # limit it will throw an exception.
                if not run and run_count >= max_run_count:
                    remove_run_count = run_count - max_run_count + 1
                    raise codechecker_api_shared.ttypes.RequestFailed(
                        codechecker_api_shared.ttypes.ErrorCode.GENERAL,
                        'You reached the maximum number of allowed runs '
                        '({0}/{1})! Please remove at least {2} run(s) before '
                        'you try it again.'.format(run_count,
                                                   max_run_count,
                                                   remove_run_count))

    @exc_to_thrift_reqfail
    @timeit
    def massStoreRun(self, name, tag, version, b64zip, force,
                     trim_path_prefixes, description):
        self.__require_store()
        start_time = time.time()

        user = self.__auth_session.user if self.__auth_session else None

        # Check constraints of the run.
        self.__check_run_limit(name)

        with DBSession(self.__Session) as session:
            ThriftRequestHandler.__store_run_lock(session, name, user)

        wrong_src_code_comments = []
        try:
            with TemporaryDirectory() as zip_dir:
                zip_size = unzip(b64zip, zip_dir)

                LOG.debug("Using unzipped folder '%s'", zip_dir)

                source_root = os.path.join(zip_dir, 'root')
                report_dir = os.path.join(zip_dir, 'reports')
                metadata_file = os.path.join(report_dir, 'metadata.json')
                skip_file = os.path.join(report_dir, 'skip_file')
                content_hash_file = os.path.join(zip_dir,
                                                 'content_hashes.json')

                skip_handler = skiplist_handler.SkipListHandler()
                if os.path.exists(skip_file):
                    LOG.debug("Pocessing skip file %s", skip_file)
                    try:
                        with open(skip_file,
                                  encoding="utf-8",
                                  errors="ignore") as sf:
                            skip_handler = \
                                skiplist_handler.SkipListHandler(sf.read())
                    except (IOError, OSError) as err:
                        LOG.error("Failed to open skip file")
                        LOG.error(err)

                filename_to_hash = util.load_json_or_empty(content_hash_file,
                                                           {})

                file_path_to_id = self.__store_source_files(source_root,
                                                            filename_to_hash,
                                                            trim_path_prefixes)

                run_history_time = datetime.now()

                metadata_parser = MetadataInfoParser()
                check_commands, check_durations, cc_version, statistics, \
                    checkers = metadata_parser.get_metadata_info(metadata_file)

                command = ''
                if len(check_commands) == 1:
                    command = list(check_commands)[0]
                elif len(check_commands) > 1:
                    command = "multiple analyze calls: " + \
                              '; '.join(check_commands)

                durations = 0
                if check_durations:
                    # Round the duration to seconds.
                    durations = int(sum(check_durations))

                # When we use multiple server instances and we try to run
                # multiple storage to each server which contain at least two
                # reports which have the same report hash and have source code
                # comments it is possible that the following exception will be
                # thrown: (psycopg2.extensions.TransactionRollbackError)
                # deadlock detected.
                # The problem is that the report hash is the key for the
                # review data table and both of the store actions try to
                # update the same review data row.
                # Neither of the two processes can continue, and they will wait
                # for each other indefinitely. PostgreSQL in this case will
                # terminate one transaction with the above exception.
                # For this reason in case of failure we will wait some seconds
                # and try to run the storage again.
                # For more information see #2655 and #2653 issues on github.
                max_num_of_tries = 3
                num_of_tries = 0
                sec_to_wait_after_failure = 60
                while True:
                    try:
                        # This session's transaction buffer stores the actual
                        # run data into the database.
                        with DBSession(self.__Session) as session:
                            # Load the lock record for "FOR UPDATE" so that the
                            # transaction that handles the run's store
                            # operations has a lock on the database row itself.
                            run_lock = session.query(RunLock) \
                                .filter(RunLock.name == name) \
                                .with_for_update(nowait=True).one()

                            # Do not remove this seemingly dummy print, we need
                            # to make sure that the execution of the SQL
                            # statement is not optimised away and the fetched
                            # row is not garbage collected.
                            LOG.debug("Storing into run '%s' locked at '%s'.",
                                      name, run_lock.locked_at)

                            # Actual store operation begins here.
                            user_name = self.__get_username()
                            run_id = \
                                store_handler.addCheckerRun(session,
                                                            command,
                                                            name,
                                                            tag,
                                                            user_name,
                                                            run_history_time,
                                                            version,
                                                            force,
                                                            cc_version,
                                                            statistics,
                                                            description)

                            self.__store_reports(session,
                                                 report_dir,
                                                 source_root,
                                                 run_id,
                                                 file_path_to_id,
                                                 run_history_time,
                                                 self.__context.severity_map,
                                                 wrong_src_code_comments,
                                                 skip_handler,
                                                 checkers,
                                                 trim_path_prefixes)

                            store_handler.setRunDuration(session,
                                                         run_id,
                                                         durations)

                            store_handler.finishCheckerRun(session, run_id)

                            session.commit()

                            LOG.info("'%s' stored results (%s KB) to run '%s' "
                                     "in %s seconds.", user_name,
                                     round(zip_size / 1024), name,
                                     round(time.time() - start_time, 2))

                            return run_id
                    except (sqlalchemy.exc.OperationalError,
                            sqlalchemy.exc.ProgrammingError) as ex:
                        num_of_tries += 1

                        if num_of_tries == max_num_of_tries:
                            raise codechecker_api_shared.ttypes.RequestFailed(
                                codechecker_api_shared.ttypes.
                                ErrorCode.DATABASE,
                                "Storing reports to the database failed: "
                                "{0}".format(ex))

                        LOG.error("Storing reports of '%s' run failed: "
                                  "%s.\nWaiting %d sec before trying to store "
                                  "it again!", name, ex,
                                  sec_to_wait_after_failure)
                        time.sleep(sec_to_wait_after_failure)
                        sec_to_wait_after_failure *= 2
        except Exception as ex:
            LOG.error("Failed to store results: %s", ex)
            import traceback
            traceback.print_exc()
            raise
        finally:
            # In any case if the "try" block's execution began, a run lock must
            # exist, which can now be removed, as storage either completed
            # successfully, or failed in a detectable manner.
            # (If the failure is undetectable, the coded grace period expiry
            # of the lock will allow further store operations to the given
            # run name.)
            with DBSession(self.__Session) as session:
                ThriftRequestHandler.__free_run_lock(session, name)

            if wrong_src_code_comments:
                raise codechecker_api_shared.ttypes.RequestFailed(
                    codechecker_api_shared.ttypes.ErrorCode.SOURCE_FILE,
                    "Multiple source code comment can be found with the same "
                    "checker name for same bug!",
                    wrong_src_code_comments)

    @exc_to_thrift_reqfail
    @timeit
    def allowsStoringAnalysisStatistics(self):
        self.__require_store()

        return True if self.__manager.get_analysis_statistics_dir() else False

    @exc_to_thrift_reqfail
    @timeit
    def getAnalysisStatisticsLimits(self):
        self.__require_store()

        cfg = dict()

        # Get the limit of failure zip size.
        failure_zip_size = self.__manager.get_failure_zip_size()
        if failure_zip_size:
            cfg[ttypes.StoreLimitKind.FAILURE_ZIP_SIZE] = failure_zip_size

        # Get the limit of compilation database size.
        compilation_database_size = \
            self.__manager.get_compilation_database_size()
        if compilation_database_size:
            cfg[ttypes.StoreLimitKind.COMPILATION_DATABASE_SIZE] = \
                compilation_database_size

        return cfg

    @exc_to_thrift_reqfail
    @timeit
    def storeAnalysisStatistics(self, run_name, b64zip):
        self.__require_store()

        report_dir_store = self.__manager.get_analysis_statistics_dir()
        if report_dir_store:
            try:
                product_dir = os.path.join(report_dir_store,
                                           self.__product.endpoint)
                # Create report store directory.
                if not os.path.exists(product_dir):
                    os.makedirs(product_dir)

                # Removes and replaces special characters in the run name.
                run_name = slugify(run_name)
                run_zip_file = os.path.join(product_dir, run_name + '.zip')
                with open(run_zip_file, 'wb') as run_zip:
                    run_zip.write(zlib.decompress(
                        base64.b64decode(b64zip.encode('utf-8'))))
                return True
            except Exception as ex:
                LOG.error(str(ex))
                return False

        return False

    @exc_to_thrift_reqfail
    @timeit
    def getAnalysisStatistics(self, run_id, run_history_id):
        self.__require_access()

        analyzer_statistics = {}

        with DBSession(self.__Session) as session:
            query = session.query(AnalyzerStatistic,
                                  Run.id)

            if run_id:
                query = query.filter(Run.id == run_id)
            elif run_history_id:
                query = query.filter(RunHistory.id == run_history_id)

            query = query \
                .outerjoin(RunHistory,
                           RunHistory.id == AnalyzerStatistic.run_history_id) \
                .outerjoin(Run,
                           Run.id == RunHistory.run_id)

            for stat, run_id in query:
                failed_files = zlib.decompress(stat.failed_files).decode(
                    'utf-8').split('\n') if stat.failed_files else None
                analyzer_version = zlib.decompress(
                    stat.version).decode('utf-8') if stat.version else None

                analyzer_statistics[stat.analyzer_type] = \
                    ttypes.AnalyzerStatistics(version=analyzer_version,
                                              failed=stat.failed,
                                              failedFilePaths=failed_files,
                                              successful=stat.successful)
        return analyzer_statistics
