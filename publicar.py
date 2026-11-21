#!/usr/bin/env python3
"""Publica no Instagram (@dr.luizleite) os posts aprovados da fila.json.

Regras de segurança:
- Só publica posts com status "aprovado" e horário já vencido.
- Sem a opção --publicar, apenas simula (nada é enviado ao Instagram).
- O token nunca é impresso. Ele vem das variáveis de ambiente:
    INSTAGRAM_TOKEN     token de acesso da Página (API do Instagram com login do Facebook)
    INSTAGRAM_USER_ID   ID da conta profissional do Instagram

Uso:
    python3 publicar.py                 simulação (dry-run)
    python3 publicar.py --publicar      publica de verdade
    python3 publicar.py --id POST_ID    limita a um post da fila
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
FILA = RAIZ / "fila.json"
VERSAO = os.environ.get("IG_API_VERSION", "v27.0")
# Login do Facebook: token da Página, endereço graph.facebook.com.
BASE = os.environ.get("IG_API_BASE", "https://graph.facebook.com") + f"/{VERSAO}"
REPO = os.environ.get("GITHUB_REPOSITORY", "LuizLeiteAdv/publicacoes-instagram")
URL_MIDIA = os.environ.get(
    "URL_MIDIA", f"https://raw.githubusercontent.com/{REPO}/main/midia"
)
# Não publica automaticamente se o horário combinado já passou há mais que isto.
TOLERANCIA = timedelta(hours=24)


class ErroPublicacao(Exception):
    pass


def chamar(metodo, caminho, token, params=None):
    params = dict(params or {})
    params["access_token"] = token
    dados = urllib.parse.urlencode(params).encode()
    if metodo == "GET":
        req = urllib.request.Request(f"{BASE}/{caminho}?{dados.decode()}")
    else:
        req = urllib.request.Request(f"{BASE}/{caminho}", data=dados, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            msg = json.loads(corpo).get("error", {}).get("message", corpo)
        except ValueError:
            msg = corpo
        raise ErroPublicacao(f"HTTP {e.code} em {metodo} {caminho}: {msg}") from None
    except urllib.error.URLError as e:
        raise ErroPublicacao(f"Falha de rede em {metodo} {caminho}: {e.reason}") from None


def conferir_imagem(url):
    """Confere se a imagem está acessível publicamente e é JPEG."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tipo = r.headers.get("Content-Type", "")
    except urllib.error.URLError as e:
        raise ErroPublicacao(f"Imagem inacessível ({url}): {e}") from None
    if "jpeg" not in tipo:
        raise ErroPublicacao(f"Imagem não é JPEG ({tipo}): {url}")


def esperar(container, token, tentativas=30):
    for _ in range(tentativas):
        r = chamar("GET", container, token, {"fields": "status_code"})
        status = r.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ErroPublicacao(f"Contêiner {container} com status {status}")
        time.sleep(4)
    raise ErroPublicacao(f"Tempo esgotado esperando o contêiner {container}")


def publicar_post(post, ig_id, token):
    urls = [f"{URL_MIDIA}/{post['id']}/{nome}" for nome in post["imagens"]]
    for u in urls:
        conferir_imagem(u)
    legenda = post["legenda"]
    if len(urls) == 1:
        container = chamar(
            "POST", f"{ig_id}/media", token, {"image_url": urls[0], "caption": legenda}
        )["id"]
    else:
        filhos = []
        for u in urls:
            filho = chamar(
                "POST", f"{ig_id}/media", token,
                {"image_url": u, "is_carousel_item": "true"},
            )["id"]
            esperar(filho, token)
            filhos.append(filho)
        container = chamar(
            "POST", f"{ig_id}/media", token,
            {"media_type": "CAROUSEL", "children": ",".join(filhos), "caption": legenda},
        )["id"]
    esperar(container, token)
    midia = chamar("POST", f"{ig_id}/media_publish", token, {"creation_id": container})["id"]
    link = ""
    try:
        link = chamar("GET", midia, token, {"fields": "permalink"}).get("permalink", "")
    except ErroPublicacao:
        pass
    return midia, link


def publicar_facebook(post, page_id, token):
    """Publica as mesmas imagens e a legenda na Página do Facebook."""
    urls = [f"{URL_MIDIA}/{post['id']}/{nome}" for nome in post["imagens"]]
    legenda = post["legenda"]
    if len(urls) == 1:
        r = chamar("POST", f"{page_id}/photos", token,
                   {"url": urls[0], "caption": legenda, "published": "true"})
        return r.get("post_id") or r["id"]
    fotos = []
    for u in urls:
        r = chamar("POST", f"{page_id}/photos", token, {"url": u, "published": "false"})
        fotos.append({"media_fbid": r["id"]})
    r = chamar("POST", f"{page_id}/feed", token,
               {"message": legenda, "attached_media": json.dumps(fotos)})
    return r["id"]


def publicar_stories_instagram(post, ig_id, token):
    """Publica cada arte vertical (campo "stories") como um Story do Instagram.

    Os IDs já publicados ficam em post["stories_instagram"], para que uma nova
    tentativa continue de onde parou, sem repetir Story.
    """
    ids = post.setdefault("stories_instagram", [])
    for nome in post.get("stories", [])[len(ids):]:
        url = f"{URL_MIDIA}/{post['id']}/{nome}"
        conferir_imagem(url)
        container = chamar(
            "POST", f"{ig_id}/media", token, {"media_type": "STORIES", "image_url": url}
        )["id"]
        esperar(container, token)
        ids.append(chamar("POST", f"{ig_id}/media_publish", token, {"creation_id": container})["id"])
    return ids


def publicar_stories_facebook(post, page_id, token):
    """Publica as mesmas artes verticais como Stories da Página do Facebook."""
    ids = post.setdefault("stories_facebook", [])
    for nome in post.get("stories", [])[len(ids):]:
        url = f"{URL_MIDIA}/{post['id']}/{nome}"
        foto = chamar("POST", f"{page_id}/photos", token, {"url": url, "published": "false"})["id"]
        r = chamar("POST", f"{page_id}/photo_stories", token, {"photo_id": foto})
        ids.append(r.get("post_id") or foto)
    return ids


def publicar_stories(post, ig_id, page_id, token):
    """Publica os Stories do post no Instagram e no Facebook. Retorna True se houve erro."""
    # Regra do Luiz (21/11/2026): nenhum Story é publicado se o post desse Story falhou.
    # Cada rede segue a sua própria regra, e a falha de uma não bloqueia a outra.
    erros = []
    total = len(post.get("stories", []))
    if len(post.get("stories_instagram", [])) < total:
        if not post.get("instagram_media_id"):
            msg = "Stories do Instagram não publicados: o post do Instagram não foi publicado."
            erros.append(msg)
            print(f"[{post['id']}] {msg}", file=sys.stderr)
        else:
            try:
                publicar_stories_instagram(post, ig_id, token)
                print(f"[{post['id']}] STORIES INSTAGRAM PUBLICADOS: {total}")
            except ErroPublicacao as e:
                erros.append(f"Instagram: {e}")
                print(f"[{post['id']}] ERRO nos Stories do Instagram: {e}", file=sys.stderr)
    if page_id and post.get("facebook", True) and len(post.get("stories_facebook", [])) < total:
        if not post.get("facebook_post_id"):
            msg = "Stories do Facebook não publicados: o post do Facebook não foi publicado."
            erros.append(msg)
            print(f"[{post['id']}] {msg}", file=sys.stderr)
        else:
            try:
                publicar_stories_facebook(post, page_id, token)
                print(f"[{post['id']}] STORIES FACEBOOK PUBLICADOS: {total}")
            except ErroPublicacao as e:
                erros.append(f"Facebook: {e}")
                print(f"[{post['id']}] ERRO nos Stories do Facebook: {e}", file=sys.stderr)
    if erros:
        post["stories_erro"] = " | ".join(erros)
    else:
        post.pop("stories_erro", None)
    return bool(erros)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--publicar", action="store_true", help="publica de verdade")
    ap.add_argument("--id", help="processa apenas este post")
    ap.add_argument("--facebook-somente", action="store_true",
                    help="publica só na Página do Facebook um post já publicado no Instagram")
    ap.add_argument("--stories-somente", action="store_true",
                    help="publica só os Stories de um post já publicado que ainda não os tem")
    ap.add_argument("--verificar", action="store_true",
                    help="só confere o token e a conta (não publica nada)")
    args = ap.parse_args()

    fila = json.loads(FILA.read_text(encoding="utf-8"))
    agora = datetime.now(timezone.utc)
    token = os.environ.get("INSTAGRAM_TOKEN", "")
    ig_id = os.environ.get("INSTAGRAM_USER_ID", "")
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
    if args.publicar and not (token and ig_id):
        print("ERRO: defina INSTAGRAM_TOKEN e INSTAGRAM_USER_ID.", file=sys.stderr)
        return 2

    if args.verificar:
        if not (token and ig_id):
            print("ERRO: defina INSTAGRAM_TOKEN e INSTAGRAM_USER_ID.", file=sys.stderr)
            return 2
        try:
            r = chamar("GET", ig_id, token, {"fields": "username"})
        except ErroPublicacao as e:
            print(f"ERRO na verificação: {e}", file=sys.stderr)
            return 1
        print(f"Conexão OK. Conta do Instagram: @{r.get('username')}")
        if page_id:
            try:
                pg = chamar("GET", page_id, token, {"fields": "name"})
            except ErroPublicacao as e:
                print(f"ERRO ao acessar a Página do Facebook: {e}", file=sys.stderr)
                return 1
            print(f"Página do Facebook OK: {pg.get('name')}")
        try:
            d = chamar("GET", "debug_token", token, {"input_token": token}).get("data", {})
            exp = d.get("expires_at", 0)
            quando = "nunca" if not exp else datetime.fromtimestamp(exp, timezone.utc).strftime("%d/%m/%Y")
            print(f"Token expira: {quando}. Tipo: {d.get('type')}. Válido: {d.get('is_valid')}")
            print("Permissões:", ", ".join(sorted(d.get("scopes", []))))
        except ErroPublicacao as e:
            print(f"Aviso: não foi possível conferir a validade do token ({e})")
        return 0

    houve_erro = False
    for post in fila["posts"]:
        if args.id and post["id"] != args.id:
            continue
        if args.facebook_somente:
            if not (args.publicar and page_id and post["status"] == "publicado"
                    and not post.get("facebook_post_id")):
                print(f"[{post['id']}] ignorado no modo Facebook")
                continue
            try:
                post["facebook_post_id"] = publicar_facebook(post, page_id, token)
                post.pop("facebook_erro", None)
                print(f"[{post['id']}] FACEBOOK PUBLICADO: {post['facebook_post_id']}")
            except ErroPublicacao as e:
                post["facebook_erro"] = str(e)
                print(f"[{post['id']}] ERRO no Facebook: {e}", file=sys.stderr)
                houve_erro = True
            FILA.write_text(json.dumps(fila, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        if args.stories_somente:
            if not (args.publicar and post["status"] == "publicado" and post.get("stories")):
                print(f"[{post['id']}] ignorado no modo Stories")
                continue
            houve_erro |= publicar_stories(post, ig_id, page_id, token)
            FILA.write_text(json.dumps(fila, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        if post["status"] != "aprovado":
            print(f"[{post['id']}] ignorado (status: {post['status']})")
            continue
        quando = datetime.fromisoformat(post["data_hora"])
        if quando > agora:
            print(f"[{post['id']}] aguardando o horário {post['data_hora']}")
            continue
        if agora - quando > TOLERANCIA:
            print(f"[{post['id']}] ATRASADO além de 24 horas. Não publicado. Revisar.", file=sys.stderr)
            houve_erro = True
            continue
        if not args.publicar:
            print(f"[{post['id']}] SIMULAÇÃO: publicaria {len(post['imagens'])} imagem(ns) agora.")
            continue
        try:
            midia, link = publicar_post(post, ig_id, token)
        except ErroPublicacao as e:
            post["status"] = "erro"
            post["erro"] = str(e)
            print(f"[{post['id']}] ERRO: {e}", file=sys.stderr)
            houve_erro = True
        else:
            post["status"] = "publicado"
            post["publicado_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            post["instagram_media_id"] = midia
            post["link"] = link
            post.pop("erro", None)
            print(f"[{post['id']}] PUBLICADO: {link or midia}")
            if page_id and post.get("facebook", True):
                try:
                    post["facebook_post_id"] = publicar_facebook(post, page_id, token)
                    print(f"[{post['id']}] FACEBOOK PUBLICADO: {post['facebook_post_id']}")
                except ErroPublicacao as e:
                    post["facebook_erro"] = str(e)
                    print(f"[{post['id']}] ERRO no Facebook: {e}", file=sys.stderr)
                    houve_erro = True
            if post.get("stories"):
                houve_erro |= publicar_stories(post, ig_id, page_id, token)
        FILA.write_text(json.dumps(fila, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if houve_erro else 0


if __name__ == "__main__":
    sys.exit(main())
