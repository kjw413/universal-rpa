# Universal RPA test harness

A deterministic, accessible Windows application used as the automation target for
the interactive end-to-end suite. It exists so the Windows runner can be
exercised against real UIA elements and real native input without ever pointing
at a customer application.

The harness imports nothing from `universal_rpa`. Under automation it behaves
like any other third-party Windows program.

## Running it

```powershell
.\.venv\Scripts\python.exe -m samples.test_harness `
  --state-file C:\Temp\harness-state.json `
  --ready-file C:\Temp\harness-ready.json
```

The ready file is written only after the window is mapped and its native handle
exists; it publishes the process id and top-level HWND so a fixture can wait on
it and then address the exact window.

| Flag | Effect |
| --- | --- |
| `--delayed-control-ms N` | Reveal `delayedControl` after `N` ms (default 500) |
| `--duplicate-selector` | Add two controls sharing AutomationId `duplicateButton` |
| `--intentional-timeout` | Never reveal `delayedControl`, so a wait must time out |
| `--lock-output` with `--lock-output-path P` | Hold an exclusive Windows lock on `P` |

## Controls

Each control's `objectName` is what Qt's Windows UIA bridge publishes as
`AutomationId`, so a recorded workflow resolves by selector alone and never needs
a coordinate fallback.

| AutomationId | Kind | Observable effect |
| --- | --- | --- |
| `normalText` | text field | `normal_text`, `set_text_count` |
| `dateText` | text field | `date_text`, `set_text_count` |
| `koreanText` | text field | `korean_text`, preloaded with `가나다라` |
| `passwordText` | password field | `password_present` only |
| `clickButton` | button | `click_count` |
| `doubleClickButton` | button | `double_click_count` |
| `dragSurface` | surface | `drag_count` after press → move → release |
| `scrollSurface` | surface | `scroll_count` per wheel notch |
| `hotkeyIndicator` | label | `hotkey_count`; `Ctrl+A` selects all in the focused field |
| `delayedControl` | button | `delayed_control_visible` once revealed |
| `openModalButton` | button | opens the owned modal, `modal_open_count` |
| `modalCloseButton` | button | closes it, `modal_close_count` |
| `duplicateButton` | button ×2 | present only with `--duplicate-selector` |
| `copyTableButton` | button | puts the fixed synthetic TSV table on the clipboard |

## What the state file may contain

Counters and the harness's own fixed synthetic values — nothing else. There is no
captured input, no clipboard body, and no operator-typed value in it, so a state
file left behind by a failed run cannot leak anything. The password field is the
clearest case: only `password_present` is recorded, never the characters.

Synthetic values are fixed constants in `state.py`: the date `2026-07-27`, the
Korean string `가나다라`, and a three-row `factory/period/quantity` table.
