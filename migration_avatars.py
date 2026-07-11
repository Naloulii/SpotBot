"""
Script de migration à lancer UNE SEULE FOIS pour :
  1) ajouter avatar_url aux entrées déjà existantes dans stats.json,
     likes.json, historique.json (et les archives stats_week_*.json)
  2) générer artists.json avec la vraie photo de chaque artiste déjà
     présent dans historique.json (via l'API publique Deezer, aucune
     clé requise — Spotify verrouille son endpoint de recherche derrière
     l'Extended Quota Mode, inatteignable pour une app perso)

Utilisation :
    python migration_avatars.py

Variable d'environnement nécessaire :
    DISCORD_TOKEN
Doit tourner dans le même dossier que tes fichiers JSON
(modifie DATA_DIR ci-dessous si besoin).
"""

import os
import json
import glob
import requests
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Tes fichiers JSON sont à la racine du dépôt, au même niveau que ce script
DATA_DIR = BASE_DIR
ARTISTS_FILE = os.path.join(DATA_DIR, "artists.json")
HISTORIQUE_FILE = os.path.join(DATA_DIR, "historique.json")

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

# Cache pour éviter de refaire un fetch_member() par fichier pour le même utilisateur
avatar_cache = {}


async def recuperer_avatar(user_id: str):
    if user_id in avatar_cache:
        return avatar_cache[user_id]

    membre = None
    for guild in client.guilds:
        membre = guild.get_member(int(user_id))
        if membre is None:
            try:
                membre = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                membre = None
            except Exception as e:
                print(f"⚠️ Erreur fetch_member({user_id}) sur {guild.name} : {e}")
                membre = None
        if membre:
            break

    if membre:
        avatar_cache[user_id] = str(membre.display_avatar.url)
    else:
        avatar_cache[user_id] = None
        print(f"❌ Membre introuvable pour l'ID {user_id} (a peut-être quitté le serveur)")

    return avatar_cache[user_id]


async def migrer_fichier_utilisateurs(chemin_fichier, cle_liste=None):
    """Pour stats.json / likes.json / historique.json : structure { user_id: {...} }"""
    if not os.path.exists(chemin_fichier):
        print(f"⏭️  {os.path.basename(chemin_fichier)} introuvable, ignoré.")
        return

    with open(chemin_fichier, "r") as f:
        data = json.load(f)

    modifie = False
    for user_id, infos in data.items():
        avatar_url = await recuperer_avatar(user_id)
        if avatar_url and infos.get("avatar_url") != avatar_url:
            infos["avatar_url"] = avatar_url
            modifie = True

    if modifie:
        with open(chemin_fichier, "w") as f:
            json.dump(data, f, indent=4)
        print(f"✅ {os.path.basename(chemin_fichier)} mis à jour.")
    else:
        print(f"ℹ️  {os.path.basename(chemin_fichier)} déjà à jour.")


# ------------------------------------------------------------------
# PARTIE ARTISTES : backfill de artists.json à partir de historique.json,
# via l'API publique Deezer (aucune clé requise).
# ------------------------------------------------------------------

def rechercher_image_artiste(nom_artiste):
    try:
        reponse = requests.get(
            "https://api.deezer.com/search/artist",
            params={"q": nom_artiste, "limit": 1},
            timeout=10
        )
        reponse.raise_for_status()
        data = reponse.json()
        items = data.get("data", [])
        if not items:
            return None
        artiste = items[0]
        return artiste.get("picture_medium") or artiste.get("picture")
    except Exception as e:
        print(f"⚠️ [Deezer] Erreur recherche artiste '{nom_artiste}' : {e}")
        return None


def migrer_artistes():
    if not os.path.exists(HISTORIQUE_FILE):
        print("⏭️  historique.json introuvable, artists.json ignoré.")
        return

    with open(HISTORIQUE_FILE, "r") as f:
        historique = json.load(f)

    # Récupère tous les noms d'artistes uniques présents dans l'historique
    tous_les_noms = set()
    for user_data in historique.values():
        for ecoute in user_data.get("ecoutes", []):
            artiste_str = ecoute.get("artiste", "")
            for nom in artiste_str.split(";"):
                nom = nom.strip()
                if nom:
                    tous_les_noms.add(nom)

    if os.path.exists(ARTISTS_FILE):
        with open(ARTISTS_FILE, "r") as f:
            cache = json.load(f)
    else:
        cache = {}

    modifie = False
    for nom in sorted(tous_les_noms):
        cle = nom.lower()
        if cle in cache:
            continue
        image_url = rechercher_image_artiste(nom)
        cache[cle] = {"nom": nom, "image_url": image_url}
        modifie = True
        statut = "✅" if image_url else "❌"
        print(f"{statut} {nom}")

    if modifie:
        with open(ARTISTS_FILE, "w") as f:
            json.dump(cache, f, indent=4)
        print("✅ artists.json mis à jour.")
    else:
        print("ℹ️  artists.json déjà à jour.")


@client.event
async def on_ready():
    print(f"🤖 Connecté en tant que {client.user} — migration en cours...")

    # Fichiers principaux
    await migrer_fichier_utilisateurs(os.path.join(DATA_DIR, "stats.json"))
    await migrer_fichier_utilisateurs(os.path.join(DATA_DIR, "likes.json"))
    await migrer_fichier_utilisateurs(os.path.join(DATA_DIR, "historique.json"))

    # Archives hebdomadaires déjà générées (stats_week_*.json)
    for archive in glob.glob(os.path.join(DATA_DIR, "stats_week_*.json")):
        await migrer_fichier_utilisateurs(archive)

    # Photos d'artistes (ne nécessite pas Discord, mais on le fait ici par simplicité)
    migrer_artistes()

    print("🎉 Migration terminée. Tu peux commit/push le dossier data et fermer ce script.")
    await client.close()


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("❌ La variable d'environnement DISCORD_TOKEN n'est pas définie.")
    client.run(DISCORD_TOKEN)

