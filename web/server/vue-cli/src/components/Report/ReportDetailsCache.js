import { ccService, handleThriftError } from "@cc-api";

// The report tree and the report view both need the opened report's details,
// which carry its whole constraint trace, so they share one response. Only the
// last report asked for is kept, and opening a report again fetches it anew.
let _last = null;

export function getReportDetails(reportId) {
  const _key = reportId.toString();
  if (_last?.key === _key) return _last.details;

  const _entry = { key: _key };
  _entry.details = new Promise(resolve => {
    ccService.getClient().getReportDetails(reportId,
      handleThriftError(resolve, err => {
        // A failed request is not shared, so the next caller retries.
        if (_last === _entry) _last = null;
        handleThriftError()(err);
      }));
  });
  _last = _entry;
  return _entry.details;
}

export function forgetReportDetails() {
  _last = null;
}
