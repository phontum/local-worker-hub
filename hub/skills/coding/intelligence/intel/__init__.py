"""Deterministic code intelligence: definitions, references, implementations, call edges, diagnostics and symbol context, with no model call.

`provider.SymbolProvider` is the interface; `lexical.LexicalProvider` implements it on the host's CodeIndex (tree-sitter and `ast`, name-based). Every
answer says how precise it is, so a semantic provider (LSP, SCIP) can later replace it behind the same tools without callers changing."""
