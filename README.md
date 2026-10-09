# Tayama

Canvas infinito para orquestrar agentes CLI (Claude Code, Codex CLI, OpenCode CLI ou qualquer comando)
em terminais PTY reais, com **cargos**, **skills** e **conexões** entre terminais.

## Executar (Linux/macOS; no Windows use WSL)
    ./run.sh          # cria .venv, instala PyQt6 + PyQt6-WebEngine + pyte e abre o app

> **Linux:** o painel web usa Chromium embarcado (QtWebEngine). Em sistemas sem user namespaces
> configurados ( containers, WSL antigo, kernels com `kernel.unprivileged_userns_clone=0`), o
> Chromium falha ao iniciar com *"Failed to move to new namespace"*. Rode com o sandbox desligado:
> `QTWEBENGINE_DISABLE_SANDBOX=1 ./run.sh`

## Como usar
1. Abra a sidebar à direita (Ctrl+B) e crie um **workspace** (nome + diretório de trabalho). Depois use **Novo agente**: a pasta vem do workspace escolhido.
2. **Cada workspace tem o seu próprio canvas.** Clicar num workspace na sidebar troca o canvas: você vê só os terminais e painéis daquele ambiente, e não os dos outros. O workspace em uso fica em negrito. Trocar de canvas não derruba nada — os terminais dos outros workspaces continuam rodando em segundo plano, e cada workspace lembra o seu próprio zoom e posição (durante a sessão).
3. Clique no ícone de link num terminal (origem) e depois no ícone de link de outro (destino): surge uma seta
   *origem → destino* = a origem pode enviar mensagens ao destino. Conecte nos dois sentidos se quiser conversa mútua.
   Botão direito na seta remove. **As conexões são só dentro do mesmo workspace** — workspaces são ambientes
   separados, então ligar um terminal de um workspace a outro é recusado e o app avisa na barra de status.
4. Ao iniciar, o agente recebe um **briefing** (cargo + skills + instruções + conectados). o ícone de prancheta reenvia.
5. Dentro de cada terminal existe o comando `tayama` (as ferramentas dos agentes):
       tayama peers
       tayama send <nome-ou-cargo> "mensagem"
       tayama broadcast "mensagem"
   A mensagem aparece no input do destino como `[tayama de <nome> (<cargo>)] ...`.
   O `broadcast` alcança **todos os terminais do workspace que está na tela** — nada atravessa a fronteira entre workspaces.
6. **Agentes/Cargos/Skills** edita agentes/cargos/skills (JSON em `~/.tayama/config.json`). Todo `.md` em
   `~/.tayama/skills/` vira uma skill. Crie quantos cargos quiser (nome, cor, prompt).

## Atalhos
Ctrl+scroll: zoom · botão do meio ou Espaço+arrastar: mover a tela · Ctrl+Shift+C/V: copiar/colar no terminal.
Clique simples no workspace na sidebar: trocar de canvas · duplo clique: ir até aquele terminal ou painel.

## Estrutura
    main.py                 janela principal
    tayama/config.py     config, cargos, skills (~/.tayama)
    tayama/terminal.py   PTY + emulação (pyte)
    tayama/ui.py         janelas, setas, canvas (uma cena por workspace), diálogos
    tayama/sidebar.py    árvore de workspaces, troca de canvas
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
