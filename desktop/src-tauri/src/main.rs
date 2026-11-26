// Impede a janela de console extra em builds de release no Windows (sem efeito no macOS).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    artigo2md_desktop_lib::run()
}
