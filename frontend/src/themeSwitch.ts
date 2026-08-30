// 主题切换器（docs/spec_ui_themes.md §3，建造者 A 交付）
// 集成员在 App.vue 侧栏用 THEME_META 渲染色板，点击 applyTheme(id)；
// 启动时 applyTheme(currentTheme())。

export const THEME_IDS = [
  "modern",
  "ink",
  "calm",
  "siam",
  "metro",
  "cyber",
  "nebula",
  "dynasty",
  "cthulhu",
  "sweetheart",
  "fortune",
  "carbon",
  "zitan",
  "huanghuali",
  "titanium",
] as const;

export type ThemeId = (typeof THEME_IDS)[number] | "classic";

export const THEME_META: Record<ThemeId, { label: string; swatch: string }> = {
  modern: {
    label: "现代简约",
    swatch: "linear-gradient(135deg, #fafafa 0%, #2563eb 100%)",
  },
  ink: {
    label: "古风水墨",
    swatch: "linear-gradient(135deg, #f5f1e6 0%, #2b2b2b 50%, #9e2b25 100%)",
  },
  calm: {
    label: "护眼模式",
    swatch: "linear-gradient(135deg, #dcead8 0%, #4a7c59 100%)",
  },
  siam: {
    label: "暹罗小猫",
    swatch: "linear-gradient(135deg, #f7efe3 0%, #6b4a2f 50%, #7aa7c7 100%)",
  },
  metro: {
    label: "现代都市",
    swatch: "linear-gradient(135deg, #e8eaed 0%, #2f3437 50%, #c9a227 100%)",
  },
  cyber: {
    label: "赛博朋克",
    swatch: "linear-gradient(135deg, #0d0f1a 0%, #22d3ee 55%, #ff2d95 100%)",
  },
  nebula: {
    label: "宇宙大战",
    swatch: "linear-gradient(135deg, #0a0e1e 0%, #7c6cf0 55%, #ff8a3d 100%)",
  },
  dynasty: {
    label: "华夏王朝",
    swatch: "linear-gradient(135deg, #f6efdb 0%, #b02418 55%, #b08d3f 100%)",
  },
  cthulhu: {
    label: "克苏鲁",
    swatch: "linear-gradient(135deg, #0c1210 0%, #3ec99a 55%, #143028 100%)",
  },
  sweetheart: {
    label: "恋爱甜心",
    swatch: "linear-gradient(135deg, #fdf2f6 0%, #c2255c 55%, #f5dbe6 100%)",
  },
  fortune: {
    label: "马上发财",
    swatch: "linear-gradient(135deg, #faf1da 0%, #c02b1a 50%, #b8860b 100%)",
  },
  carbon: {
    label: "碳纤维",
    swatch: "linear-gradient(135deg, #111316 0%, #2e343b 55%, #d93636 100%)",
  },
  zitan: {
    label: "紫檀木",
    swatch: "linear-gradient(135deg, #1d1410 0%, #4a3325 55%, #d9a441 100%)",
  },
  huanghuali: {
    label: "黄花梨",
    swatch: "linear-gradient(135deg, #f2e5cd 0%, #c9a468 55%, #7a4a1d 100%)",
  },
  titanium: {
    label: "钛合金",
    swatch: "linear-gradient(135deg, #dfe3e7 0%, #9aa4ac 55%, #3d6ea5 100%)",
  },
  classic: {
    label: "经典牛皮纸",
    swatch: "linear-gradient(135deg, #f3edde 0%, #b63a2b 100%)",
  },
};

const STORAGE_KEY = "fe-theme";

const THEME_ID_SET: ReadonlySet<string> = new Set<string>([...THEME_IDS, "classic"]);

function isThemeId(value: string): value is ThemeId {
  return THEME_ID_SET.has(value);
}

export function applyTheme(id: ThemeId): void {
  if (id === "classic") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = id;
  }
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // SSR 或隐私模式下 localStorage 不可用，静默忽略即可。
  }
}

export function currentTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null && isThemeId(stored)) return stored;
  } catch {
    // 读取失败（隐私模式等）按未存储处理。
  }
  return "classic";
}
