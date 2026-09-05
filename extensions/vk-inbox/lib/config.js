// Build-time configuration baked by scripts/build.sh. Safe defaults live
// here; a deployed package carries the real global host endpoint/token so a
// fresh install on any machine (e.g. the Mac) talks to UserIO without any
// manual options-page setup. chrome.storage.local values still override.
(function (root) {
  root.USERIO_CONFIG = {
    endpoint: "",
    token: "",
    agentId: "",
  };
})(typeof self !== "undefined" ? self : this);
