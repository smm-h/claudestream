# Unknown keys in definition files silently ignored

## Context

Found during a code audit (2026-07) of definition-file loading.

## Problem

Unknown keys in a JSON definition file are silently ignored on load. For agent-authored files
this is the deadliest failure mode: a typo'd field name (`modle` for `model`, a misremembered
option) vanishes without an error, and the definition behaves as if the field were never
written. The author gets no signal that their intent was dropped.

## Suggested fix

1. Red test: a definition file containing one unknown top-level key and one unknown nested key
   must fail loading with diagnostics naming each unknown key (ideally with a did-you-mean
   suggestion against the known-key set).
2. Make unknown keys a hard error at every nesting level of the loader. If a genuine extension
   zone is needed, make it one explicit, named passthrough field — never a relaxed policy on
   the whole document.
3. Scan existing definition files for keys that are currently being ignored before turning the
   error on — surface them as the fix's first victims and correct them in the same change.

## Affected area

Definition-file loading/parsing.

## Effort

Small-medium: the strict check is simple; the pre-existing-files scan and cleanup is the
necessary companion so the hard error lands green.
