//! Limites de leitura (MB decimal; ver plan.md, "Limites rígidos de leitura", e research Decision 3).

/// Acima disso o documento é marcado como grande (aviso do FR-034). Valor de projeto (block-005).
pub const LARGE_FILE_THRESHOLD_BYTES: u64 = 5_000_000;

/// Teto rígido de leitura de um `.md`; acima disso o erro é `tooLarge` (block-006).
pub const MAX_MARKDOWN_BYTES: u64 = 50_000_000;

/// Teto rígido de leitura de uma imagem; acima disso o erro é `tooLarge` (block-006).
pub const MAX_IMAGE_BYTES: u64 = 20_000_000;

/// Tamanho do arquivo de log do lado Rust antes da rotação (plan.md, "Log do lado Rust").
pub const MAX_LOG_BYTES: u64 = 1_000_000;
