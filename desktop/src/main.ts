import { mount } from "svelte";
import "katex/dist/katex.min.css";
import App from "./App.svelte";
import "./styles/app.css";

const target = document.getElementById("app");
if (!target) {
  throw new Error("Elemento raiz #app não encontrado");
}

export default mount(App, { target });
