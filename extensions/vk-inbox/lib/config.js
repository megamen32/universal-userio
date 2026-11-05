// Build-time public defaults. User-specific OAuth tokens are issued after
// installation and never belong in this file or the shared package.
(function (root) {
  root.USERIO_CONFIG = {
    endpoint: "https://msg.bezrabotnyi.com",
    token: "",
    agentId: "",
  };
})(typeof self !== "undefined" ? self : this);
