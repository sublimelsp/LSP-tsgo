# LSP-tsgo

TypeScript and JavaScript support for Sublime's LSP plugin provided through [typescript](https://github.com/microsoft/TypeScript) (go based).

> [!NOTE]
> `typescript-go` is now merged into the main `typescript` repo. This package will likely follow and get merged into [LSP-typescript](https://packagecontrol.io/packages/LSP-typescript) once Typescript 7 gains the tsserver API.

## Installation

 * Install [`LSP`](https://packagecontrol.io/packages/LSP) and `LSP-tsgo` from Package Control.
 * Restart Sublime.

## Configuration

Open the configuration file using the Command Palette `Preferences: LSP-tsgo Settings` command or open it from the Sublime menu.

## Content mappers

TypeScript 7.1 can type-check other file types, such as Vue single-file components, through the content mappers in the `contentMappers` field of a tsconfig. Content mappers are enabled by default. They run code from the project's `node_modules`, so disable them for projects that you don't trust, in the `.sublime-project` file:

```jsonc
{
    "settings": {
        "LSP": {
            "LSP-tsgo": {
                "initialization_options": {
                    "runExternalCode": false
                }
            }
        }
    }
}
```

Content mappers run on the same Node.js as the server.

### Contributing content mappers from other packages

Other packages, such as [LSP-vue](https://packagecontrol.io/packages/LSP-vue), can contribute content mappers with a `typescript-content-mappers.json` file in the package root:

```jsonc
[
    {
        // Name of the content mapper's npm package.
        "name": "@vue/content-mapper",
        // File extensions that the content mapper handles.
        "extensions": [".vue"],
        // Views that this server attaches to, unless `runExternalCode` is disabled.
        "selector": "text.html.vue",
        // Content mapper options for files outside any tsconfig.
        "options": { "format": "sfc" }
    }
]
```

Once the content mapper is installed, and before every start of the package's server, the package reports the directory containing the content mapper's npm package, usually a `node_modules` directory:

```py
sublime.run_command('lsp_tsgo_set_content_mapper_location', {
    'name': '@vue/content-mapper',
    'location': str(server_directory / 'node_modules'),
})
```

The server uses these content mappers for files outside any tsconfig. It starts them with the `typescript.contentMapper` manifest of their `package.json`.

Other packages can also open a session of the TypeScript API on this server. They send a `custom/initializeAPISession` request to the `LSP-tsgo` session. The response has the API `pipe` and `packageJsonPath`, the `package.json` of the TypeScript installation that runs the server.

## Go to Source Definition

`LSP: Goto Definition` on a symbol from a dependency lands in its `.d.ts`. The `LSP-tsgo: Goto Source Definition` command (`lsp_tsgo_goto_source_definition`) asks the server for the implementation behind that declaration instead: the `.js` shipped next to the typings, or the original `.ts` when a declaration map is available. When the server has nothing to map (the symbol is already in source, or the server is too old to support the request) the command falls back to the ordinary definition, so it can take over a definition key binding:

```json
{
    "keys": ["super+i"],
    "command": "lsp_tsgo_goto_source_definition",
    "args": {"fallback": true},
    "context": [
        {"key": "lsp.session_with_name", "operand": "LSP-tsgo"}
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
