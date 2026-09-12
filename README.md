# LSP-tsgo

TypeScript and JavaScript support for Sublime's LSP plugin provided through [typescript-go](https://github.com/microsoft/typescript-go) (and more specifically the [`@typescript/native-preview`](https://www.npmjs.com/package/@typescript/native-preview) package).

> [!NOTE]
> Microsoft plans to merge `typescript-go` into the main `typescript` branch at some point. This package will likely follow and get merged into [LSP-typescript](https://packagecontrol.io/packages/LSP-typescript) then.

## Installation

 * Install [`LSP`](https://packagecontrol.io/packages/LSP) and `LSP-tsgo` from Package Control.
 * Restart Sublime.

## Configuration

Open the configuration file using the Command Palette `Preferences: LSP-tsgo Settings` command or open it from the Sublime menu.

## Go to Source Definition

`LSP: Goto Definition` on a symbol from a dependency lands in its `.d.ts`. The `LSP-tsgo: Goto Source Definition` command (`lsp_tsgo_goto_source_definition`) asks the server for the implementation behind that declaration instead: the `.js` shipped next to the typings, or the original `.ts` when a declaration map is available. When the server has nothing to map (the symbol is already in source, or the server is too old to support the request) the command falls back to the ordinary definition, so it can take over a definition key binding:

```json
{
    "keys": ["super+i"],
    "command": "lsp_tsgo_goto_source_definition",
    "args": {"side_by_side": false, "force_group": true, "group": -1, "fallback": true},
    "context": [
        {"key": "lsp.session_with_capability", "operand": "definitionProvider"},
        {"key": "selector", "operator": "equal", "operand": "source.js, source.jsx, source.ts, source.tsx"}
    ]
}
```

Pass `"fallback": false` to get a status message instead of the fallback.

## Code Actions on Save

The server supports the following code actions that can be specified in the global `lsp_code_actions_on_save` setting and run on saving files:

 - `source.fixAll` - despite the name, fixes a couple of specific issues: unreachable code, await in non-async functions, incorrectly implemented interface
 - `source.organizeImports` - organizes and removes unused imports
 - `source.removeUnusedImports` - removes unused imports
 - `source.sortImports` - sorts imports
