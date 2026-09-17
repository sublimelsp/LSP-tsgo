# LSP-tsgo

TypeScript and JavaScript support for Sublime's LSP plugin provided through [typescript](https://github.com/microsoft/TypeScript) (go based).

> [!NOTE]
> `typescript-go` is now merged into the main `typescript` repo. This package will likely follow and get merged into [LSP-typescript](https://packagecontrol.io/packages/LSP-typescript) once Typescript 7 gains the tsserver API.

## Installation

 * Install [`LSP`](https://packagecontrol.io/packages/LSP) and `LSP-tsgo` from Package Control.
 * Restart Sublime.

## Configuration

Open the configuration file using the Command Palette `Preferences: LSP-tsgo Settings` command or open it from the Sublime menu.

## Code Actions on Save

The server supports the following code actions that can be specified in the global `lsp_code_actions_on_save` setting and run on saving files:

 - `source.fixAll` - despite the name, fixes a couple of specific issues: unreachable code, await in non-async functions, incorrectly implemented interface
 - `source.organizeImports` - organizes and removes unused imports
 - `source.removeUnusedImports` - removes unused imports
 - `source.sortImports` - sorts imports
