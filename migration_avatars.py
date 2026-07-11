"""
Script de migration à lancer UNE SEULE FOIS pour ajouter avatar_url
aux entrées déjà existantes dans stats.json, likes.json, historique.json
(et dans les archives stats_week_*.json si tu en as déjà).

Utilisation :
    python migration_avatars.py

Il réutilise les mêmes variables d'environnement que bot.py
(DISCORD_TOKEN) et doit tourner dans le même dossier /data que le bot
(ou modifie DATA_DIR ci-dessous si besoin).
"""

import os
import json
import glob
import asyncio
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Tes fichiers JSON sont à la racine du dépôt, au même niveau que ce script
DATA_DIR = BASE_DIR

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

    print("🎉 Migration terminée. Tu peux commit/push le dossier data et fermer ce script.")
    await client.close()


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("❌ La variable d'environnement DISCORD_TOKEN n'est pas définie.")
    client.run(DISCORD_TOKEN)
