// Navegação por teclado da árvore (tarefa 4.3.5, decisão do operador 2.2.3). Lógica pura, sem DOM:
// o componente transforma os `treeitem` visíveis em `NavItem` e aplica a ação devolvida.

export interface NavItem {
  /** `aria-level` do item (1 = raiz da árvore). */
  level: number;
  /** Pasta que pode ser expandida ou recolhida. */
  expandable: boolean;
  expanded: boolean;
}

export type NavAction =
  | { type: "focus"; index: number }
  | { type: "expand"; index: number }
  | { type: "collapse"; index: number }
  | { type: "activate"; index: number }
  | { type: "none" };

const NONE: NavAction = { type: "none" };

/** Índice do item pai (o último anterior com nível menor), ou -1 se não houver. */
export function parentIndex(items: readonly NavItem[], index: number): number {
  const level = items[index]?.level ?? 0;
  for (let i = index - 1; i >= 0; i -= 1) {
    if (items[i]!.level < level) return i;
  }
  return -1;
}

/**
 * Ação para a tecla `key` com o item `current` ativo. Setas, Home e End movem o foco; seta direita
 * expande ou entra na pasta; seta esquerda recolhe ou sobe ao pai; Enter e Espaço ativam o item
 * (abrir arquivo ou alternar pasta).
 */
export function treeKeyAction(key: string, items: readonly NavItem[], current: number): NavAction {
  if (items.length === 0) return NONE;
  const last = items.length - 1;
  const index = current < 0 ? 0 : Math.min(current, last);
  const item = items[index]!;
  switch (key) {
    case "ArrowDown":
      return current < 0 ? { type: "focus", index: 0 } : { type: "focus", index: Math.min(index + 1, last) };
    case "ArrowUp":
      return current < 0 ? { type: "focus", index: 0 } : { type: "focus", index: Math.max(index - 1, 0) };
    case "Home":
      return { type: "focus", index: 0 };
    case "End":
      return { type: "focus", index: last };
    case "ArrowRight":
      if (item.expandable && !item.expanded) return { type: "expand", index };
      if (item.expandable && item.expanded && index < last && items[index + 1]!.level > item.level) {
        return { type: "focus", index: index + 1 };
      }
      return NONE;
    case "ArrowLeft": {
      if (item.expandable && item.expanded) return { type: "collapse", index };
      const parent = parentIndex(items, index);
      return parent >= 0 ? { type: "focus", index: parent } : NONE;
    }
    case "Enter":
    case " ":
      return current < 0 ? NONE : { type: "activate", index };
    default:
      return NONE;
  }
}

/** Lê `aria-level` e `aria-expanded` de elementos `treeitem` (ordem do documento). */
export function navItemsFromElements(
  elements: ArrayLike<{ getAttribute(name: string): string | null }>,
): NavItem[] {
  return Array.from(elements, (element) => {
    const expanded = element.getAttribute("aria-expanded");
    return {
      level: Number(element.getAttribute("aria-level") ?? "1") || 1,
      expandable: expanded !== null,
      expanded: expanded === "true",
    };
  });
}

/** Passo de teclado da barra lateral redimensionável (separador com `role="separator"`). */
export function sidebarKeyWidth(key: string, width: number, min: number, max: number, step = 16): number | null {
  switch (key) {
    case "ArrowLeft":
      return Math.max(min, width - step);
    case "ArrowRight":
      return Math.min(max, width + step);
    case "Home":
      return min;
    case "End":
      return max;
    default:
      return null;
  }
}

/** Identificador de DOM de um item da árvore (usado por `aria-activedescendant`). */
export function treeItemId(path: string, prefix: string): string {
  return `${prefix}-${path.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}
