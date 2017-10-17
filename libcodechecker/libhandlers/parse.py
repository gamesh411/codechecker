# -------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -------------------------------------------------------------------------
"""
Defines the CodeChecker action for parsing a set of analysis results into a
human-readable format.
"""

from collections import Counter
import argparse
import json
import os
import sys

from plist_to_html import PlistToHtml

from libcodechecker import generic_package_context
from libcodechecker import generic_package_suppress_handler
from libcodechecker import util
from libcodechecker.analyze.analyzers import analyzer_types
# TODO: This is a cross-subpackage reference...
from libcodechecker.log import build_action
from libcodechecker.logger import add_verbose_arguments
from libcodechecker.logger import LoggerFactory
from libcodechecker.output_formatters import twodim_to_str

LOG = LoggerFactory.get_new_logger('PARSE')


def get_argparser_ctor_args():
    """
    This method returns a dict containing the kwargs for constructing an
    argparse.ArgumentParser (either directly or as a subparser).
    """

    return {
        'prog': 'CodeChecker parse',
        'formatter_class': argparse.ArgumentDefaultsHelpFormatter,

        # Description is shown when the command's help is queried directly
        'description': "Parse and pretty-print the summary and results from "
                       "one or more 'codechecker-analyze' result files.",

        # Help is shown when the "parent" CodeChecker command lists the
        # individual subcommands.
        'help': "Print analysis summary and results in a human-readable "
                "format."
    }


def add_arguments_to_parser(parser):
    """
    Add the subcommand's arguments to the given argparse.ArgumentParser.
    """

    parser.add_argument('input',
                        type=str,
                        nargs='*',
                        metavar='file/folder',
                        default=os.path.join(util.get_default_workspace(),
                                             'reports'),
                        help="The analysis result files and/or folders "
                             "containing analysis results which should be "
                             "parsed and printed.")

    parser.add_argument('-t', '--type', '--input-format',
                        dest="input_format",
                        required=False,
                        choices=['plist'],
                        default='plist',
                        help="Specify the format the analysis results were "
                             "created as.")

    output_opts = parser.add_argument_group("export arguments")
    output_opts.add_argument('-e', '--export',
                             dest="export",
                             required=False,
                             choices=['html'],
                             help="Specify extra output format type.")

    output_opts.add_argument('-o', '--output',
                             dest="output_path",
                             help="Store the output in the given folder.")

    output_opts.add_argument('-c', '--clean',
                             dest="clean",
                             required=False,
                             action='store_true',
                             default=argparse.SUPPRESS,
                             help="Delete output results stored in the output "
                                  "directory. (By default, it would keep "
                                  "output files and overwrites only those "
                                  "that belongs to a plist file given by the "
                                  "input argument.")

    parser.add_argument('--suppress',
                        type=str,
                        dest="suppress",
                        default=argparse.SUPPRESS,
                        required=False,
                        help="Path of the suppress file to use. Records in "
                             "the suppress file are used to suppress the "
                             "display of certain results when parsing the "
                             "analyses' report. (Reports to an analysis "
                             "result can also be suppressed in the source "
                             "code -- please consult the manual on how to "
                             "do so.) NOTE: The suppress file relies on the "
                             "\"bug identifier\" generated by the analyzers "
                             "which is experimental, take care when relying "
                             "on it.")

    parser.add_argument('--export-source-suppress',
                        dest="create_suppress",
                        action="store_true",
                        required=False,
                        default=argparse.SUPPRESS,
                        help="Write suppress data from the suppression "
                             "annotations found in the source files that were "
                             "analyzed earlier that created the results. "
                             "The suppression information will be written "
                             "to the parameter of '--suppress'.")

    parser.add_argument('--print-steps',
                        dest="print_steps",
                        action="store_true",
                        required=False,
                        default=argparse.SUPPRESS,
                        help="Print the steps the analyzers took in finding "
                             "the reported defect.")

    add_verbose_arguments(parser)

    def __handle(args):
        """Custom handler for 'parser' so custom error messages can be
        printed without having to capture 'parser' in main."""

        def arg_match(options):
            return util.arg_match(options, sys.argv[1:])

        # --export cannot be specified without --output.
        export = ['-e', '--export']
        output = ['-o', '--output']
        if any(arg_match(export)) and not any(arg_match(output)):
            parser.error("argument --export: not allowed without "
                         "argument --output")

        # If everything is fine, do call the handler for the subcommand.
        main(args)

    parser.set_defaults(func=__handle)


def parse(f, context, metadata_dict, suppress_handler, steps):
    """
    Prints the results in the given file to the standard output in a human-
    readable format.

    Returns the report statistics collected by the result handler.
    """

    if not f.endswith(".plist"):
        LOG.info("Skipping input file '" + f + "' as it is not a plist.")
        return {}

    LOG.debug("Parsing input file '" + f + "'")

    buildaction = build_action.BuildAction()

    rh = analyzer_types.construct_parse_handler(buildaction,
                                                f,
                                                context.severity_map,
                                                suppress_handler,
                                                steps)

    # Set some variables of the result handler to use the saved file.
    rh.analyzer_returncode = 0
    rh.analyzer_result_file = f
    rh.analyzer_cmd = ""

    if 'result_source_files' in metadata_dict and \
            f in metadata_dict['result_source_files']:
        rh.analyzed_source_file = \
            metadata_dict['result_source_files'][f]
    else:
        rh.analyzed_source_file = "UNKNOWN"

    return rh.handle_results()


def main(args):
    """
    Entry point for parsing some analysis results and printing them to the
    stdout in a human-readable format.
    """

    context = generic_package_context.get_context()

    # To ensure the help message prints the default folder properly,
    # the 'default' for 'args.input' is a string, not a list.
    # But we need lists for the foreach here to work.
    if isinstance(args.input, str):
        args.input = [args.input]

    original_cwd = os.getcwd()

    suppress_handler = None
    if 'suppress' in args:
        __make_handler = False
        if not os.path.isfile(args.suppress):
            if 'create_suppress' in args:
                with open(args.suppress, 'w') as _:
                    # Just create the file.
                    __make_handler = True
                    LOG.info("Will write source-code suppressions to "
                             "suppress file.")
            else:
                LOG.warning("Suppress file '" + args.suppress + "' given, but "
                            "it does not exist -- will not suppress anything.")
        else:
            __make_handler = True

        if __make_handler:
            suppress_handler = generic_package_suppress_handler.\
                GenericSuppressHandler(args.suppress,
                                       'create_suppress' in args)
    elif 'create_suppress' in args:
        LOG.error("Can't use '--export-source-suppress' unless '--suppress "
                  "SUPPRESS_FILE' is also given.")
        sys.exit(2)

    for input_path in args.input:

        input_path = os.path.abspath(input_path)
        os.chdir(original_cwd)
        LOG.debug("Parsing input argument: '" + input_path + "'")

        export = args.export if 'export' in args else None
        if export is not None and export == 'html':
            output_path = os.path.abspath(args.output_path)

            LOG.info("Generating html output files:")
            PlistToHtml.parse(input_path,
                              output_path,
                              context.path_plist_to_html_dist,
                              'clean' in args)
            continue

        severity_stats = Counter({})
        file_stats = Counter({})
        report_count = Counter({})

        files = []
        metadata_dict = {}
        if os.path.isfile(input_path):
            files.append(input_path)

        elif os.path.isdir(input_path):
            metadata_file = os.path.join(input_path, "metadata.json")
            if os.path.exists(metadata_file):
                with open(metadata_file, 'r') as metadata:
                    metadata_dict = json.load(metadata)
                    LOG.debug(metadata_dict)

                if 'working_directory' in metadata_dict:
                    os.chdir(metadata_dict['working_directory'])

            _, _, file_names = next(os.walk(input_path), ([], [], []))
            files = [os.path.join(input_path, file_name) for file_name
                     in file_names]

        for file_path in files:
            report_stats = parse(file_path,
                                 context,
                                 metadata_dict,
                                 suppress_handler,
                                 'print_steps' in args)

            severity_stats.update(Counter(report_stats.get('severity',
                                          {})))
            file_stats.update(Counter(report_stats.get('files', {})))
            report_count.update(Counter(report_stats.get('reports', {})))

        print("\n----==== Summary ====----")

        if file_stats:
            vals = [[os.path.basename(k), v] for k, v in
                    dict(file_stats).items()]
            keys = ['Filename', 'Report count']
            table = twodim_to_str('table', keys, vals, 1, True)
            print(table)

        if severity_stats:
            vals = [[k, v] for k, v in dict(severity_stats).items()]
            keys = ['Severity', 'Report count']
            table = twodim_to_str('table', keys, vals, 1, True)
            print(table)

        report_count = dict(report_count).get("report_count", 0)
        print("----=================----")
        print("Total number of reports: {}".format(report_count))
        print("----=================----")

    os.chdir(original_cwd)
