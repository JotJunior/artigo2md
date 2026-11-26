import { describe, expect, it } from "vitest";
import {
  navItemsFromElements,
  parentIndex,
  sidebarKeyWidth,
  treeItemId,
  treeKeyAction,
  type NavItem,
} from "../src/lib/tree-nav";

// Árvore:  0 pasta A (aberta) / 1 arquivo a1 / 2 pasta B (fechada, nível 2) / 3 arquivo z (raiz)
const items: NavItem[] = [
  { level: 1, expandable: true, expanded: true },
  { level: 2, expandable: false, expanded: false },
  { level: 2, expandable: true, expanded: false },
  { level: 1, expandable: false, expanded: false },
];

describe("navegação por teclado da árvore (tarefa 4.3.5)", () => {
  it("setas para cima e para baixo movem o foco e param nas pontas", () => {
    expect(treeKeyAction("ArrowDown", items, 0)).toEqual({ type: "focus", index: 1 });
    expect(treeKeyAction("ArrowDown", items, 3)).toEqual({ type: "focus", index: 3 });
    expect(treeKeyAction("ArrowUp", items, 2)).toEqual({ type: "focus", index: 1 });
    expect(treeKeyAction("ArrowUp", items, 0)).toEqual({ type: "focus", index: 0 });
  });

  it("sem item ativo, a primeira seta vai ao primeiro item", () => {
    expect(treeKeyAction("ArrowDown", items, -1)).toEqual({ type: "focus", index: 0 });
    expect(treeKeyAction("ArrowUp", items, -1)).toEqual({ type: "focus", index: 0 });
    expect(treeKeyAction("Enter", items, -1)).toEqual({ type: "none" });
  });

  it("Home e End vão ao primeiro e ao último", () => {
    expect(treeKeyAction("Home", items, 2)).toEqual({ type: "focus", index: 0 });
    expect(treeKeyAction("End", items, 0)).toEqual({ type: "focus", index: 3 });
  });

  it("seta direita expande a pasta fechada ou entra na aberta", () => {
    expect(treeKeyAction("ArrowRight", items, 2)).toEqual({ type: "expand", index: 2 });
    expect(treeKeyAction("ArrowRight", items, 0)).toEqual({ type: "focus", index: 1 });
    expect(treeKeyAction("ArrowRight", items, 1)).toEqual({ type: "none" });
  });

  it("seta esquerda recolhe a pasta aberta ou sobe ao pai", () => {
    expect(treeKeyAction("ArrowLeft", items, 0)).toEqual({ type: "collapse", index: 0 });
    expect(treeKeyAction("ArrowLeft", items, 1)).toEqual({ type: "focus", index: 0 });
    expect(treeKeyAction("ArrowLeft", items, 3)).toEqual({ type: "none" });
  });

  it("Enter e Espaço ativam o item", () => {
    expect(treeKeyAction("Enter", items, 1)).toEqual({ type: "activate", index: 1 });
    expect(treeKeyAction(" ", items, 2)).toEqual({ type: "activate", index: 2 });
  });

  it("teclas desconhecidas e árvore vazia não fazem nada", () => {
    expect(treeKeyAction("a", items, 0)).toEqual({ type: "none" });
    expect(treeKeyAction("ArrowDown", [], -1)).toEqual({ type: "none" });
  });

  it("parentIndex encontra o pai pelo nível", () => {
    expect(parentIndex(items, 1)).toBe(0);
    expect(parentIndex(items, 2)).toBe(0);
    expect(parentIndex(items, 3)).toBe(-1);
  });

  it("navItemsFromElements lê aria-level e aria-expanded", () => {
    const make = (attrs: Record<string, string>) => ({ getAttribute: (n: string) => attrs[n] ?? null });
    expect(
      navItemsFromElements([
        make({ "aria-level": "1", "aria-expanded": "true" }),
        make({ "aria-level": "2" }),
        make({ "aria-level": "2", "aria-expanded": "false" }),
      ]),
    ).toEqual([
      { level: 1, expandable: true, expanded: true },
      { level: 2, expandable: false, expanded: false },
      { level: 2, expandable: true, expanded: false },
    ]);
  });

  it("treeItemId gera identificadores estáveis e seguros", () => {
    expect(treeItemId("Artigos/a b.md", "tree")).toBe("tree-Artigos_a_b_md");
  });
});

describe("teclado da barra lateral", () => {
  it("setas e Home/End ajustam a largura dentro da faixa", () => {
    expect(sidebarKeyWidth("ArrowRight", 260, 200, 480)).toBe(276);
    expect(sidebarKeyWidth("ArrowLeft", 260, 200, 480)).toBe(244);
    expect(sidebarKeyWidth("ArrowLeft", 205, 200, 480)).toBe(200);
    expect(sidebarKeyWidth("ArrowRight", 475, 200, 480)).toBe(480);
    expect(sidebarKeyWidth("Home", 300, 200, 480)).toBe(200);
    expect(sidebarKeyWidth("End", 300, 200, 480)).toBe(480);
    expect(sidebarKeyWidth("x", 300, 200, 480)).toBeNull();
  });
});
