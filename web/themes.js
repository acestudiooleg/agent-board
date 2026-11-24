// Теми: два «космоси» (стилі в index.html) і класичні схеми розробників (themes.css + палітра нижче).
// Палітра фарбує і дошку, і термінал картки. ansi: 8 звичайних і 8 яскравих кольорів.
window.Themes = (() => {
  const T = (id, name, light, bg, bg2, line, fg, muted, accent, green, warn, violet, red, ansi) =>
    ({id, name, light, bg, bg2, line, fg, muted, accent, green, warn, violet, red, ansi: ansi.split(" ").map(c => "#" + c)});
  const list = [
    T("dracula", "Dracula", false, "#282a36", "#21222c", "#44475a", "#f8f8f2", "#8b95c9", "#bd93f9", "#50fa7b", "#ffb86c", "#ff79c6", "#ff5555",
      "21222c ff5555 50fa7b f1fa8c bd93f9 ff79c6 8be9fd f8f8f2 6272a4 ff6e6e 69ff94 ffffa5 d6acff ff92df a4ffff ffffff"),
    T("onedark", "One Dark", false, "#282c34", "#21252b", "#3e4451", "#abb2bf", "#7f8794", "#61afef", "#98c379", "#e5c07b", "#c678dd", "#e06c75",
      "282c34 e06c75 98c379 e5c07b 61afef c678dd 56b6c2 abb2bf 5c6370 e06c75 98c379 e5c07b 61afef c678dd 56b6c2 ffffff"),
    T("tokyonight", "Tokyo Night", false, "#1a1b26", "#16161e", "#292e42", "#c0caf5", "#737aa2", "#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#f7768e",
      "15161e f7768e 9ece6a e0af68 7aa2f7 bb9af7 7dcfff a9b1d6 414868 f7768e 9ece6a e0af68 7aa2f7 bb9af7 7dcfff c0caf5"),
    T("catppuccin", "Catppuccin Mocha", false, "#1e1e2e", "#181825", "#313244", "#cdd6f4", "#7f849c", "#89b4fa", "#a6e3a1", "#f9e2af", "#cba6f7", "#f38ba8",
      "45475a f38ba8 a6e3a1 f9e2af 89b4fa f5c2e7 94e2d5 bac2de 585b70 f38ba8 a6e3a1 f9e2af 89b4fa f5c2e7 94e2d5 a6adc8"),
    T("nord", "Nord", false, "#2e3440", "#3b4252", "#4c566a", "#d8dee9", "#8f9bb3", "#88c0d0", "#a3be8c", "#ebcb8b", "#b48ead", "#bf616a",
      "3b4252 bf616a a3be8c ebcb8b 81a1c1 b48ead 88c0d0 e5e9f0 4c566a bf616a a3be8c ebcb8b 81a1c1 b48ead 8fbcbb eceff4"),
    T("gruvbox", "Gruvbox Dark", false, "#282828", "#1d2021", "#504945", "#ebdbb2", "#a89984", "#fe8019", "#b8bb26", "#fabd2f", "#d3869b", "#fb4934",
      "282828 cc241d 98971a d79921 458588 b16286 689d6a a89984 928374 fb4934 b8bb26 fabd2f 83a598 d3869b 8ec07c ebdbb2"),
    T("monokai", "Monokai", false, "#272822", "#1e1f1c", "#49483e", "#f8f8f2", "#90887a", "#66d9ef", "#a6e22e", "#e6db74", "#ae81ff", "#f92672",
      "272822 f92672 a6e22e f4bf75 66d9ef ae81ff a1efe4 f8f8f2 75715e f92672 a6e22e f4bf75 66d9ef ae81ff a1efe4 f9f8f5"),
    T("solarized", "Solarized Dark", false, "#002b36", "#073642", "#1c4c58", "#93a1a1", "#657b83", "#268bd2", "#859900", "#b58900", "#6c71c4", "#dc322f",
      "073642 dc322f 859900 b58900 268bd2 d33682 2aa198 eee8d5 586e75 cb4b16 859900 b58900 268bd2 6c71c4 2aa198 fdf6e3"),
    T("github", "GitHub Dark", false, "#0d1117", "#161b22", "#30363d", "#e6edf3", "#8b949e", "#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#ff7b72",
      "484f58 ff7b72 3fb950 d29922 58a6ff bc8cff 39c5cf b1bac4 6e7681 ffa198 56d364 e3b341 79c0ff d2a8ff 56d4dd ffffff"),
    T("phosphor", "Фосфор", false, "#050a05", "#0a140a", "#1d4a1d", "#8cff8c", "#4fa64f", "#39ff14", "#39ff14", "#ffd75f", "#7dffc0", "#ff6b5e",
      "0a140a ff6b5e 39ff14 ffd75f 5fd7af 7dffc0 5fffd7 8cff8c 2f6f2f ff8f85 7dff5f ffe48f 8fffd0 a8ffd8 9fffe8 d8ffd8"),
    T("githublight", "GitHub Light", true, "#ffffff", "#f6f8fa", "#d0d7de", "#1f2328", "#656d76", "#0969da", "#1a7f37", "#9a6700", "#8250df", "#cf222e",
      "24292f cf222e 116329 4d2d00 0969da 8250df 1b7c83 6e7781 57606a a40e26 1a7f37 633c01 218bff a475f9 3192aa 8c959f"),
    T("latte", "Catppuccin Latte", true, "#eff1f5", "#e6e9ef", "#ccd0da", "#4c4f69", "#6c6f85", "#1e66f5", "#40a02b", "#df8e1d", "#8839ef", "#d20f39",
      "5c5f77 d20f39 40a02b df8e1d 1e66f5 ea76cb 179299 acb0be 6c6f85 d20f39 40a02b df8e1d 1e66f5 ea76cb 179299 bcc0cc"),
    T("solarizedlight", "Solarized Light", true, "#fdf6e3", "#eee8d5", "#d6cfb8", "#586e75", "#839496", "#268bd2", "#859900", "#b58900", "#6c71c4", "#dc322f",
      "073642 dc322f 859900 b58900 268bd2 d33682 2aa198 eee8d5 002b36 cb4b16 586e75 657b83 839496 6c71c4 93a1a1 fdf6e3"),
  ];
  const SPACE = {glare: "🌌 Сяйво", deep: "🌑 Глибокий космос"};
  const SPACE_TERM = {background: "#0b1524", foreground: "#e8f1f8", cursor: "#43e6ff", selectionBackground: "#2b5876",
    black: "#1b2536", red: "#ff5c8a", green: "#a6e22e", yellow: "#f4bf75", blue: "#66b3ff", magenta: "#c39bff", cyan: "#66e6ef", white: "#e8f1f8",
    brightBlack: "#7f90a5", brightRed: "#ff7fa3", brightGreen: "#b9f04a", brightYellow: "#ffd590", brightBlue: "#8cc6ff", brightMagenta: "#d6b8ff", brightCyan: "#8ff3fa", brightWhite: "#ffffff"};
  const NAMES = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];

  // ---------- контраст (WCAG) ----------
  const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const hex = c => "#" + c.map(x => Math.round(x).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, k) => hex(rgb(a).map((x, i) => x + (rgb(b)[i] - x) * k));
  const lum = h => { const [r, g, b] = rgb(h).map(x => (x /= 255) <= .03928 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4); return .2126 * r + .7152 * g + .0722 * b; };
  const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  // Колір тексту, читний на обох поверхнях: підтягуємо до чорного чи білого, доки не стане 4.5:1.
  function legible(color, t, min = 4.5) {
    const to = t.light ? "#000000" : "#ffffff";
    for (let k = 0; k <= 1; k += .04) { const c = mix(color, to, k); if (contrast(c, t.bg) >= min && contrast(c, t.bg2) >= min) return c; }
    return to;
  }
  const onColor = c => contrast("#000000", c) >= contrast("#ffffff", c) ? "#000000" : "#ffffff";
  function vars(t) {
    const out = {bg: t.bg, bg2: t.bg2, line: t.line, "line-hi": mix(t.line, t.fg, .3)};
    for (const k of ["fg", "muted", "accent", "green", "warn", "violet", "red"]) out[k] = legible(t[k], t);
    out["on-accent"] = onColor(out.accent);
    out.strong = t.light ? "#000000" : "#ffffff";
    return out;
  }
  function termTheme(t) {
    const out = {background: t.bg, foreground: legible(t.fg, t), cursor: t.accent, cursorAccent: t.bg, selectionBackground: mix(t.bg, t.accent, .35)};
    t.ansi.forEach((c, i) => { const n = NAMES[i % 8]; out[i < 8 ? n : "bright" + n[0].toUpperCase() + n.slice(1)] = c; });
    return out;
  }

  // ---------- застосування ----------
  const root = document.documentElement, KEY = "agent-board-theme";
  let id = "glare";
  const find = x => list.find(t => t.id === x);
  function apply(next) {
    const t = find(next);
    id = t || SPACE[next] ? next : "glare";
    root.classList.toggle("theme-deep", id === "deep");
    root.classList.toggle("flat", !!t);
    root.classList.toggle("light", !!t?.light);
    for (const [k, v] of Object.entries(vars(t || list[0]))) t ? root.style.setProperty("--t-" + k, v) : root.style.removeProperty("--t-" + k);
    const meta = document.querySelector("meta[name=theme-color]");
    if (meta) meta.content = t ? t.bg2 : "#050c1c";
    document.dispatchEvent(new Event("themechange"));
  }
  function set(next) { apply(next); try { localStorage.setItem(KEY, id); } catch (e) {} }
  try { id = localStorage.getItem(KEY) || "glare"; } catch (e) {}
  apply(id);
  function fill(select) {
    const group = (label, items) => `<optgroup label="${label}">${items.map(([v, n]) => `<option value="${v}">${n}</option>`).join("")}</optgroup>`;
    const of = light => list.filter(t => t.light === light).map(t => [t.id, t.name]);
    select.innerHTML = group("Космос", Object.entries(SPACE)) + group("Темні", of(false)) + group("Світлі", of(true));
    select.value = id;
    select.addEventListener("change", () => set(select.value));
  }
  return {list, vars, termTheme, contrast, set, fill, get id() { return id; },
    term: () => find(id) ? termTheme(find(id)) : SPACE_TERM,
    // Claude Code малює під темний фон; на світлому xterm сам підтягує контраст тексту.
    termContrast: () => find(id)?.light ? 4.5 : 1};
})();
