// Налаштування вигляду таблиці гравців: компактний режим і видимість колонок.
// Стан зберігається в localStorage і застосовується через класи на #playersTable,
// тому не залежить від перерендеру tbody (renderScan).
(function () {
  const STORAGE_KEY = "cs2TableView.v1";
  const table = document.getElementById("playersTable");
  const compactToggle = document.getElementById("compactToggle");
  const columnsList = document.getElementById("columnsMenuList");
  if (!table || !compactToggle || !columnsList) return;

  const headers = Array.from(table.querySelectorAll("thead th"));
  // Колонка 1 (гравець) завжди видима: без неї рядок втрачає сенс.
  const columns = headers
    .map((th, i) => ({
      index: i + 1,
      key: th.dataset.key || th.textContent.trim(),
      label: th.textContent.trim(),
      toggleable: i > 0,
    }))
    .filter((c) => c.toggleable);

  function loadState() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      return {
        compact: raw.compact === true,
        hidden: Array.isArray(raw.hidden) ? raw.hidden.map(String) : [],
      };
    } catch (_err) {
      return { compact: false, hidden: [] };
    }
  }

  let state = loadState();

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_err) {
      // localStorage може бути недоступний (приватний режим); лишаємо стан у пам'яті.
    }
  }

  function applyState() {
    table.classList.toggle("is-compact", state.compact);
    compactToggle.checked = state.compact;
    columns.forEach((c) => {
      const hidden = state.hidden.includes(c.key);
      table.classList.toggle(`hide-col-${c.index}`, hidden);
      const box = columnsList.querySelector(`input[data-col-key="${CSS.escape(c.key)}"]`);
      if (box) box.checked = !hidden;
    });
  }

  columnsList.innerHTML = columns
    .map(
      (c) =>
        `<label class="columns-menu-item"><input type="checkbox" data-col-key="${c.key.replace(/"/g, "&quot;")}" checked /> ${c.label.replace(/</g, "&lt;")}</label>`
    )
    .join("");

  compactToggle.addEventListener("change", () => {
    state.compact = compactToggle.checked;
    saveState();
    applyState();
  });

  columnsList.addEventListener("change", (e) => {
    const box = e.target.closest('input[type="checkbox"][data-col-key]');
    if (!box) return;
    const key = box.dataset.colKey;
    const others = state.hidden.filter((k) => k !== key);
    state.hidden = box.checked ? others : [...others, key];
    saveState();
    applyState();
  });

  applyState();
})();
