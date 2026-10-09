# Tayama

Canvas infinito para orquestrar agentes CLI (Claude Code, Codex CLI, OpenCode CLI ou qualquer comando)
em terminais PTY reais, com **cargos**, **skills** e **conexões** entre terminais.

## Executar (Linux/macOS; no Windows use WSL)
    ./run.sh          # cria .venv, instala PyQt6 + pyte e abre o app

## Como usar
1. Abra a sidebar à direita (Ctrl+B) e crie um **workspace** (nome + diretório de trabalho). Depois use **Novo agente**: a pasta vem do workspace escolhido.
2. Clique no ícone de link num terminal (origem) e depois no ícone de link de outro (destino): surge uma seta
   *origem → destino* = a origem pode enviar mensagens ao destino. Conecte nos dois sentidos se quiser conversa mútua.
   Botão direito na seta remove.
3. Ao iniciar, o agente recebe um **briefing** (cargo + skills + instruções + conectados). o ícone de prancheta reenvia.
4. Dentro de cada terminal existe o comando `tayama` (as ferramentas dos agentes):
       tayama peers
       tayama send <nome-ou-cargo> "mensagem"
       tayama broadcast "mensagem"
   A mensagem aparece no input do destino como `[tayama de <nome> (<cargo>)] ...`.
5. **Agentes/Cargos/Skills** edita agentes/cargos/skills (JSON em `~/.tayama/config.json`). Todo `.md` em
   `~/.tayama/skills/` vira uma skill. Crie quantos cargos quiser (nome, cor, prompt).

## Atalhos
Ctrl+scroll: zoom · botão do meio ou Espaço+arrastar: mover a tela · Ctrl+Shift+C/V: copiar/colar no terminal.

## Estrutura
    main.py                 janela principal
    tayama/config.py     config, cargos, skills (~/.tayama)
    tayama/terminal.py   PTY + emulação (pyte)
    tayama/ui.py         janelas, setas, canvas, diálogos
    tayama/bridge.py     servidor local de mensagens
    bin/tayama           CLI usada pelos agentes

## Comando não encontrado?
Os agentes iniciam via `$SHELL -l -i -c "exec <comando>"`, então usam o mesmo PATH do seu terminal (zsh/bash).
Para desativar em algum agente, adicione `"login_shell": false` na config dele.

## Aviso "confia nesta pasta?"
Antes de abrir Claude Code / Codex, o tayama marca a pasta como confiável em `~/.claude.json` e
`~/.codex/config.toml`. Para desligar num agente: `"auto_trust": false`. Para outro CLI, use `"trust": "claude"` ou `"codex"`.

## Sem avisos de atualização
Claude Code (`DISABLE_AUTOUPDATER`/`DISABLE_UPDATES`), Codex (`-c check_for_update_on_startup=false`) e
OpenCode (`OPENCODE_DISABLE_AUTOUPDATE`) iniciam com atualizações desligadas. Para permitir: `"no_update_prompts": false` no agente.
