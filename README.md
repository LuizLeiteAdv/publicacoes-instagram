# Publicações do Instagram @dr.luizleite

Publicação automática das artes aprovadas, pela API oficial do Instagram.

## Como funciona

1. As artes aprovadas (JPEG) ficam em `midia/<id-do-post>/`.
2. A fila `fila.json` guarda, para cada post, o horário, as imagens e a legenda.
3. O fluxo do GitHub Actions (`.github/workflows/publicar.yml`) roda às segundas, quartas e sextas, a partir das 09h (Brasília), e publica os posts com status `aprovado` e horário vencido.

## Status de um post

| Status | Significado |
|---|---|
| `rascunho` | Ainda não aprovado. Nunca é publicado. |
| `aprovado` | Aprovado pelo Luiz. Será publicado no horário. |
| `publicado` | Já foi ao ar. Guarda o link e a data. |
| `erro` | Falhou. A mensagem fica em `erro`. |

## Segurança

- Este repositório é **público** porque a API da Meta baixa as imagens de um endereço aberto. Só entram aqui conteúdos que vão ao ar de qualquer forma.
- O token do Instagram e o ID da conta ficam nos **segredos** do GitHub (`INSTAGRAM_TOKEN` e `INSTAGRAM_USER_ID`). Nunca no código.
- Sem a opção `--publicar`, o script apenas simula.
