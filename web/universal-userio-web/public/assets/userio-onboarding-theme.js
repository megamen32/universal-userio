(() => {
  const stored = localStorage.getItem("theme")
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true
  const theme = stored === "light" || stored === "dark"
    ? stored
    : prefersDark ? "dark" : "light"
  document.documentElement.dataset.userioTheme = theme
})()
