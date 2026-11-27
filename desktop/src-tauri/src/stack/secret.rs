//! Tipo `Secret` e mascaramento (spec FR-011, SC-007; plan.md M10, M19; research Decision 13).
//!
//! Credenciais existem só no `.env` (0600) e na memória do núcleo, dentro de `Secret`. O tipo não
//! implementa `Debug`, `Display` nem `Serialize`: o valor só sai por `Secret::expose`, usado
//! unicamente para montar o ambiente do processo (`Command::env`). Toda linha de saída técnica
//! (scripts, Compose, worker, models) passa por `Masker::mask` antes de ir para buffer,
//! `technicalLog`, `log.rs` ou detalhe de `error.rs`; o resultado é um `Masked`, o único tipo que
//! o log do app aceita.

use std::sync::atomic::{compiler_fence, Ordering};

/// Texto que o mascaramento substitui no lugar de um valor secreto.
pub const MASK: &str = "***";

/// Valor secreto. Sem `Debug`, `Display` nem `Serialize`; zerado ao ser descartado.
pub struct Secret {
    value: String,
}

impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Self {
            value: value.into(),
        }
    }

    /// Único acesso ao valor: só para `Command::env` e para escrever o `.env`. Nunca formatar o
    /// retorno em mensagem, log ou erro.
    pub fn expose(&self) -> &str {
        &self.value
    }

    pub fn is_empty(&self) -> bool {
        self.value.is_empty()
    }
}

impl Clone for Secret {
    fn clone(&self) -> Self {
        Self::new(self.value.clone())
    }
}

impl Drop for Secret {
    fn drop(&mut self) {
        wipe(&mut self.value);
    }
}

/// Zera os bytes da string no lugar (o tamanho não muda).
fn wipe(value: &mut str) {
    // SAFETY: NUL é UTF-8 válido e ocupa um byte, então a string continua válida;
    // `write_volatile` impede o otimizador de eliminar a escrita antes de liberar o buffer.
    unsafe {
        for byte in value.as_bytes_mut() {
            std::ptr::write_volatile(byte, 0);
        }
    }
    compiler_fence(Ordering::SeqCst);
}

/// Linha de texto já mascarada. Só `Masker::mask` (ou `Masked::non_secret`, para textos que não
/// têm como carregar credencial) a constrói; o log do app só aceita este tipo.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Masked(String);

impl Masked {
    /// Para texto que não passa por credencial (por exemplo, erro de E/S de arquivo do
    /// visualizador). Saída de processo externo NUNCA entra por aqui: use `Masker::mask`.
    pub fn non_secret(text: &str) -> Self {
        Self(text.to_string())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_string(self) -> String {
        self.0
    }

    /// Corta em `max_bytes` (respeitando o limite de caractere UTF-8). O corte vem DEPOIS do
    /// mascaramento: um valor secreto não fica pela metade no fim da linha.
    pub fn truncated(mut self, max_bytes: usize) -> Self {
        if self.0.len() > max_bytes {
            let mut end = max_bytes;
            while !self.0.is_char_boundary(end) {
                end -= 1;
            }
            self.0.truncate(end);
        }
        self
    }
}

/// Conjunto de valores secretos conhecidos que devem sumir de qualquer linha.
#[derive(Clone, Default)]
pub struct Masker {
    secrets: Vec<Secret>,
}

impl Masker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Registra um valor conhecido. Valor vazio é ignorado (mascarar "" apagaria a linha).
    pub fn register(&mut self, secret: &Secret) {
        if secret.is_empty() || self.secrets.iter().any(|s| s.expose() == secret.expose()) {
            return;
        }
        self.secrets.push(secret.clone());
        // Valores mais longos primeiro: um segredo contido noutro não deixa resto à mostra.
        self.secrets
            .sort_by_key(|s| std::cmp::Reverse(s.expose().len()));
    }

    /// Substitui toda ocorrência de cada valor conhecido por `***`.
    pub fn mask(&self, line: &str) -> Masked {
        let mut out = line.to_string();
        for secret in &self.secrets {
            if out.contains(secret.expose()) {
                out = out.replace(secret.expose(), MASK);
            }
        }
        Masked(out)
    }
}

/// `n_bytes` bytes de `/dev/urandom` em hexadecimal minúsculo (`2 * n_bytes` caracteres). Usado
/// para as senhas geradas (16 bytes = 32 caracteres, research Decision 5) e para nomes de
/// arquivos temporários; nunca para algo que precise de mais que isso.
pub fn random_hex(n_bytes: usize) -> std::io::Result<String> {
    use std::io::Read;
    let mut bytes = vec![0u8; n_bytes];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    let mut out = String::with_capacity(n_bytes * 2);
    for byte in &bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::{AppError, ErrorCode};
    use std::fmt::{Debug, Display};

    // Verificação em tempo de compilação: `Secret` não implementa `Debug`, `Display` nem
    // `Serialize`. Se alguém derivar um deles, estas expressões ficam ambíguas e o teste não compila.
    const _: fn() = || {
        trait AmbiguousIfDebug<A> {
            fn probe() {}
        }
        impl<T: ?Sized> AmbiguousIfDebug<()> for T {}
        impl<T: ?Sized + Debug> AmbiguousIfDebug<u8> for T {}
        let _ = <Secret as AmbiguousIfDebug<_>>::probe;

        trait AmbiguousIfDisplay<A> {
            fn probe() {}
        }
        impl<T: ?Sized> AmbiguousIfDisplay<()> for T {}
        impl<T: ?Sized + Display> AmbiguousIfDisplay<u8> for T {}
        let _ = <Secret as AmbiguousIfDisplay<_>>::probe;

        trait AmbiguousIfSerialize<A> {
            fn probe() {}
        }
        impl<T: ?Sized> AmbiguousIfSerialize<()> for T {}
        impl<T: ?Sized + serde::Serialize> AmbiguousIfSerialize<u8> for T {}
        let _ = <Secret as AmbiguousIfSerialize<_>>::probe;
    };

    const PASSWORD: &str = "Xk9-senha-gerada-7Qz";

    fn masker() -> Masker {
        let mut m = Masker::new();
        m.register(&Secret::new(PASSWORD));
        m
    }

    #[test]
    fn mask_replaces_every_occurrence() {
        let m = masker();
        let line = format!("amqp://app:{PASSWORD}@127.0.0.1 e de novo {PASSWORD}");
        let out = m.mask(&line);
        assert!(!out.as_str().contains(PASSWORD));
        assert_eq!(out.as_str(), "amqp://app:***@127.0.0.1 e de novo ***");
    }

    #[test]
    fn mask_keeps_lines_without_secret_and_ignores_empty_secret() {
        let mut m = masker();
        m.register(&Secret::new(""));
        assert_eq!(m.mask("linha comum").as_str(), "linha comum");
        assert_eq!(m.mask("").as_str(), "");
    }

    #[test]
    fn longer_secret_wins_over_contained_one() {
        let mut m = Masker::new();
        m.register(&Secret::new("abc"));
        m.register(&Secret::new("abcdef"));
        assert_eq!(m.mask("valor abcdef fim").as_str(), "valor *** fim");
    }

    #[test]
    fn register_is_idempotent() {
        let mut m = masker();
        m.register(&Secret::new(PASSWORD));
        assert_eq!(m.secrets.len(), 1);
    }

    #[test]
    fn password_never_appears_in_error_or_command_formatting() {
        let m = masker();
        let secret = Secret::new(PASSWORD);

        // Erro montado a partir de linha mascarada: nem o texto técnico nem a serialização
        // para a interface carregam a senha.
        let masked = m.mask(&format!("falha ao conectar com {PASSWORD}"));
        let err = AppError::new(ErrorCode::ScriptFailed, masked.as_str());
        let mut captured = vec![
            format!("{err}"),
            format!("{err:?}"),
            err.detail().to_string(),
            serde_json::to_string(&err).unwrap(),
            format!("{masked:?}"),
        ];

        // `Command` só recebe o valor no `env`; a formatação de erro de spawn e o texto padrão
        // do erro de E/S não podem trazê-lo (a regra é nunca formatar `Command` com `{:?}`).
        let spawn_err = std::process::Command::new("/nonexistent/artigo2md-fake")
            .env("ARTIGO2MD_SECRET", secret.expose())
            .spawn()
            .unwrap_err();
        captured.push(m.mask(&spawn_err.to_string()).into_string());
        captured.push(format!("{spawn_err:?}"));

        for text in captured {
            assert!(!text.contains(PASSWORD), "vazou em: {text}");
        }
    }

    #[test]
    fn random_hex_has_the_requested_size_and_differs_between_calls() {
        let a = random_hex(16).unwrap();
        let b = random_hex(16).unwrap();
        assert_eq!(a.len(), 32);
        assert!(a
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)));
        assert_ne!(a, b);
    }

    #[test]
    fn wipe_zeroes_every_byte_and_keeps_the_length() {
        let mut value = String::from(PASSWORD);
        wipe(&mut value);
        assert_eq!(value.len(), PASSWORD.len());
        assert!(value.bytes().all(|b| b == 0));
    }
}
