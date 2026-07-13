import os
import io
import json
import time
import asyncio
import datetime
import zoneinfo
import requests
import discord
from discord import app_commands
from discord.ext import commands, tasks
from colorthief import ColorThief
from git import Repo

# Définition globale du fuseau horaire de Paris
PARIS_TZ = zoneinfo.ZoneInfo("Europe/Paris")

# Configuration du Bot Discord
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True

# Définition de l'activité avec le bouton de redirection vers ton GitHub Pages
activite_profil = discord.Activity(
    type=discord.ActivityType.playing,
    name="SpotBot-data",
    buttons=[
        {
            "label": "Aller sur le Dashboard", 
            "url": https://naloulii.github.io/SpotBot-data/
        }
    ]
)

bot = commands.Bot(command_prefix="!", intents=intents, activity=activite_profil)

# ==========================================
#          CONFIGURATION SÉCURISÉE
# ==========================================
SALON_MUSIQUE_ID = 1520393495544594472 
DASHBOARD_URL = "https://naloulii.github.io/SpotBot-data/"

# Récupération des jetons secrets via l'hébergeur Cloud (Railway)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME")
GITHUB_PUBLIC_DATA_REPO_NAME = os.getenv("GITHUB_PUBLIC_DATA_REPO_NAME", "SpotBot-data")
# ==========================================

# Configuration des chemins locaux dans le conteneur Docker
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PUBLIC_DATA_DIR = os.path.join(BASE_DIR, "public_data")

STATS_FILE = os.path.join(DATA_DIR, "stats.json")
LIKES_FILE = os.path.join(DATA_DIR, "likes.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORIQUE_FILE = os.path.join(DATA_DIR, "historique.json")
ARTISTS_FILE = os.path.join(DATA_DIR, "artists.json")

FICHIERS_PUBLICS = ["stats.json", "likes.json", "historique.json", "artists.json"]

ecoutes_en_cours = {}
verrous_anti_spam = {} 

GITHUB_REPO_URL = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}.git"
GITHUB_PUBLIC_DATA_REPO_URL = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_PUBLIC_DATA_REPO_NAME}.git"

if not os.path.exists(DATA_DIR):
    print("🚀 Premier lancement sur le Cloud : Création du dossier data et clonage...")
    repo = Repo.clone_from(GITHUB_REPO_URL, DATA_DIR)
else:
    try:
        repo = Repo(DATA_DIR)
        print("📌 Dépôt Git local détecté dans /data.")
    except Exception:
        print("⚠️ Erreur dossier data, re-clonage automatique...")
        import shutil
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        repo = Repo.clone_from(GITHUB_REPO_URL, DATA_DIR)

if not os.path.exists(PUBLIC_DATA_DIR):
    print("🚀 Clonage du dépôt public de données...")
    public_repo = Repo.clone_from(GITHUB_PUBLIC_DATA_REPO_URL, PUBLIC_DATA_DIR)
else:
    try:
        public_repo = Repo(PUBLIC_DATA_DIR)
        print("📌 Dépôt Git public détecté dans /public_data.")
    except Exception:
        print("⚠️ Erreur dossier public_data, re-clonage automatique...")
        import shutil
        shutil.rmtree(PUBLIC_DATA_DIR, ignore_errors=True)
        public_repo = Repo.clone_from(GITHUB_PUBLIC_DATA_REPO_URL, PUBLIC_DATA_DIR)

# --- FONCTION DE SAUVEGARDE GITHUB (Toutes les 15 minutes) ---
def _sauvegarde_github_bloquante():
    try:
        repo.remotes.origin.pull()
        
        fichiers_a_ajouter = []
        for root, dirs, files in os.walk(DATA_DIR):
            for file in files:
                if file.endswith(".json"):
                    rel_path = os.path.relpath(os.path.join(root, file), DATA_DIR)
                    fichiers_a_ajouter.append(rel_path)
                
        if fichiers_a_ajouter:
            repo.index.add(fichiers_a_ajouter)
            maintenant = datetime.datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
            repo.index.commit(f"🤖 Auto-Save : Synchronisation des données ({maintenant})")
            repo.remotes.origin.push()
            print(f"📦 [GitHub] Données synchronisées avec succès : {fichiers_a_ajouter}")
    except Exception as e:
        print(f"⚠️ [GitHub] Erreur de synchronisation automatique : {e}")

    try:
        import shutil as _shutil

        try:
            public_repo.remotes.origin.pull()
        except Exception:
            pass

        fichiers_publies = []
        for nom_fichier in FICHIERS_PUBLICS:
            source = os.path.join(DATA_DIR, nom_fichier)
            destination = os.path.join(PUBLIC_DATA_DIR, nom_fichier)
            if os.path.exists(source):
                _shutil.copyfile(source, destination)
                fichiers_publies.append(nom_fichier)

        import glob as _glob
        for archive_path in _glob.glob(os.path.join(DATA_DIR, "stats_week_*.json")):
            nom_archive = os.path.basename(archive_path)
            destination = os.path.join(PUBLIC_DATA_DIR, nom_archive)
            _shutil.copyfile(archive_path, destination)
            fichiers_publies.append(nom_archive)

        if fichiers_publies:
            public_repo.index.add(fichiers_publies)

            try:
                a_des_changements = public_repo.is_dirty() or not public_repo.head.is_valid()
            except Exception:
                a_des_changements = True 

            if a_des_changements:
                maintenant = datetime.datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
                public_repo.index.commit(f"🤖 Auto-Save : Données publiques ({maintenant})")
                try:
                    public_repo.remotes.origin.push()
                except Exception:
                    branche = public_repo.active_branch.name
                    public_repo.git.push("--set-upstream", "origin", branche)
                print(f"📦 [GitHub public] Données publiées avec succès : {fichiers_publies}")
    except Exception as e:
        print(f"⚠️ [GitHub public] Erreur de synchronisation : {e}")


@tasks.loop(minutes=15)
async def sauvegarde_periodique_github():
    await asyncio.to_thread(_sauvegarde_github_bloquante)

# Fonctions de gestion de données locales (JSON)
def charger_stats():
    try:
        with open(STATS_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_stats(stats):
    with open(STATS_FILE, "w") as f: json.dump(stats, f, indent=4)

def charger_likes():
    try:
        with open(LIKES_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_likes(likes):
    with open(LIKES_FILE, "w") as f: json.dump(likes, f, indent=4)

def charger_config():
    try:
        with open(CONFIG_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {"message_aide_id": None, "message_top_id": None}

def sauvegarder_config(config):
    with open(CONFIG_FILE, "w") as f: json.dump(config, f, indent=4)

def charger_historique():
    try:
        with open(HISTORIQUE_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_historique(historique):
    with open(HISTORIQUE_FILE, "w") as f: json.dump(historique, f, indent=4)

def charger_artistes_cache():
    try:
        with open(ARTISTS_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_artistes_cache(cache):
    with open(ARTISTS_FILE, "w") as f: json.dump(cache, f, indent=4)


def rechercher_image_artiste(nom_artiste):
    for tentative in range(3):
        try:
            reponse = requests.get(
                "https://api.deezer.com/search/artist",
                params={"q": nom_artiste, "limit": 1},
                timeout=10
            )
            reponse.raise_for_status()
            data = reponse.json()

            if "error" in data:
                print(f"⏳ [Deezer] Quota atteint pour '{nom_artiste}', nouvelle tentative dans 2s...")
                time.sleep(2)
                continue

            items = data.get("data", [])
            if not items:
                return None
            artiste = items[0]
            return artiste.get("picture_medium") or artiste.get("picture")
        except Exception as e:
            print(f"⚠️ [Deezer] Erreur recherche artiste '{nom_artiste}' : {e}")
            return None

    print(f"❌ [Deezer] Abandon pour '{nom_artiste}' après plusieurs tentatives (quota).")
    return None


def mettre_a_jour_cache_artistes(chaine_artistes):
    if not chaine_artistes:
        return

    noms = [a.strip() for a in chaine_artistes.split(";") if a.strip()]
    if not noms:
        return

    cache = charger_artistes_cache()
    modifie = False

    for nom in noms:
        cle = nom.lower()
        if cle in cache and cache[cle].get("image_url"):
            continue
        image_url = rechercher_image_artiste(nom)
        cache[cle] = {"nom": nom, "image_url": image_url}
        modifie = True

    if modifie:
        sauvegarder_artistes_cache(cache)


def enregistrer_stat_membre(membre):
    user_id = str(membre.id)
    stats = charger_stats()
    if user_id not in stats:
        stats[user_id] = {"username": membre.name, "display_name": membre.display_name, "avatar_url": str(membre.display_avatar.url), "count": 0}
    stats[user_id]["username"] = membre.name
    stats[user_id]["display_name"] = membre.display_name
    stats[user_id]["avatar_url"] = str(membre.display_avatar.url)
    stats[user_id]["count"] += 1
    sauvegarder_stats(stats)

def enregistrer_like_membre(membre, titre, artiste, url):
    user_id = str(membre.id)
    likes = charger_likes()
    if user_id not in likes:
        likes[user_id] = {"username": membre.name, "display_name": membre.display_name, "avatar_url": str(membre.display_avatar.url), "liste": []}
    likes[user_id]["username"] = membre.name
    likes[user_id]["display_name"] = membre.display_name
    likes[user_id]["avatar_url"] = str(membre.display_avatar.url)
    
    deja_like = any(track['url'] == url for track in likes[user_id]["liste"])
    if deja_like:
        likes[user_id]["liste"] = [t for t in likes[user_id]["liste"] if t['url'] != url]
        sauvegarder_likes(likes)
        return False
    else:
        likes[user_id]["liste"].append({"titre": titre, "artiste": artiste, "url": url})
        sauvegarder_likes(likes)
        return True

def ajouter_a_l_historique(membre, titre, artiste, url, track_id):
    user_id = str(membre.id)
    historique = charger_historique()
    if user_id not in historique:
        historique[user_id] = {"username": membre.name, "display_name": membre.display_name, "avatar_url": str(membre.display_avatar.url), "ecoutes": []}
    historique[user_id]["username"] = membre.name
    historique[user_id]["display_name"] = membre.display_name
    historique[user_id]["avatar_url"] = str(membre.display_avatar.url)
    
    if historique[user_id]["ecoutes"]:
        derniere_ecoute = historique[user_id]["ecoutes"][0]
        if derniere_ecoute.get("track_id") == track_id:
            try:
                date_derniere = datetime.datetime.strptime(derniere_ecoute["date"], "%d/%m/%Y %H:%M")
                date_derniere = date_derniere.replace(tzinfo=PARIS_TZ)
                if (datetime.datetime.now(PARIS_TZ) - date_derniere).total_seconds() < 15:
                    return 
            except Exception: pass

    maintenant = datetime.datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    historique[user_id]["ecoutes"].insert(0, {
        "date": maintenant, 
        "titre": titre, 
        "artiste": artiste, 
        "url": url, 
        "track_id": track_id,
        "status": "En cours..."
    })
    historique[user_id]["ecoutes"] = historique[user_id]["ecoutes"][:100]
    sauvegarder_historique(historique)

    mettre_a_jour_cache_artistes(artiste)

def mettre_a_jour_historique_fin(membre, track_id, temps_ecoule, duree_totale):
    user_id = str(membre.id)
    historique = charger_historique()
    if user_id not in historique:
        return

    ecoutes = historique[user_id]["ecoutes"]

    index_actuel = None
    for i, ecoute in enumerate(ecoutes):
        if ecoute.get("track_id") == track_id and ecoute["status"] == "En cours...":
            index_actuel = i
            break
    if index_actuel is None:
        return

    ecoute_actuelle = ecoutes[index_actuel]
    ecoute_actuelle["temps_ecoute_secondes"] = round(temps_ecoule, 1)

    temps_cumule = temps_ecoule
    for ecoute_precedente in ecoutes[index_actuel + 1:]:
        if ecoute_precedente.get("track_id") != track_id:
            continue
        if ecoute_precedente["status"] == "🎉 Écouté en entier":
            break
        temps_cumule += ecoute_precedente.get("temps_ecoute_secondes", 0)

    valide = duree_totale > 0 and (temps_cumule / duree_totale) >= 0.96

    if valide:
        ecoute_actuelle["status"] = "🎉 Écouté en entier"
    else:
        m, s = divmod(int(temps_ecoule), 60)
        ecoute_actuelle["status"] = f"⏱️ Écouté pendant {m}m {s:02d}s"

    sauvegarder_historique(historique)

    if valide:
        enregistrer_stat_membre(membre)


# --- TASK : TOUS LES LUNDIS 00:00 (HEURE DE PARIS) ---
@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=PARIS_TZ))
async def classement_hebdomadaire_auto():
    if datetime.datetime.now(PARIS_TZ).weekday() != 0:
        return

    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return

    stats = charger_stats()
    config = charger_config()
    
    embed = discord.Embed(
        title="🏆 Classement de la Semaine Dernière", 
        color=discord.Color.gold(), 
        timestamp=datetime.datetime.now(PARIS_TZ)
    )
    
    if not stats:
        embed.description = "Aucune musique n'a été validée la semaine dernière ! 🎧"
    else:
        classement = sorted(stats.items(), key=lambda item: item[1]["count"], reverse=True)
        texte = ""
        for index, (u_id, data) in enumerate(classement[:10], start=1):
            nom = data.get("display_name", data.get("username", "Inconnu"))
            medailles = {1: "🥇", 2: "🥈", 3: "🥉"}
            texte += f"{medailles.get(index, f'`#{index}`')} **{nom}** — {data['count']} morceaux validés\n"
        embed.description = texte

    msg_top_id = config.get("message_top_id")
    message_existe = False
    if msg_top_id:
        try:
            msg_existant = await salon.fetch_message(msg_top_id)
            await msg_existant.edit(embed=embed)
            message_existe = True
        except Exception: pass
    if not message_existe:
        nouveau_msg = await salon.send(embed=embed)
        config["message_top_id"] = nouveau_msg.id
        sauvegarder_config(config)

    num_semaine = datetime.datetime.now(PARIS_TZ).strftime("%V_%Y")
    archive_file = os.path.join(DATA_DIR, f"stats_week_{num_semaine}.json")
    with open(archive_file, "w") as f:
        json.dump(stats, f, indent=4)

    sauvegarder_stats({})


def generer_embed_aide():
    embed = discord.Embed(
        title="🎵 Bienvenue sur SpotBot ! 🤖",
        description=(
            "Ce salon affiche l'activité musicale des membres en temps réel. "
            "Les messages se mettent à jour dynamiquement et disparaissent dès que l'écoute s'arrête.\n\n"
            "---"
        ),
        color=discord.Color.from_rgb(30, 215, 96),
        timestamp=datetime.datetime.now(PARIS_TZ)
    )
    embed.add_field(
        name="📚 Commandes disponibles :",
        value=(
            "**/top** : Classement hebdomadaire des plus grands auditeurs. 🏆\n"
            "**/likes** : La liste complète de tes morceaux favoris. ❤️\n"
            "**/history [page] [membre]** : Historique d'écoute (le tien ou celui d'un ami via son @). 🕒"
        ),
        inline=False
    )
    embed.add_field(
        name="⭐ Fonctionnalités :",
        value="• Clique sur le bouton **🤍 Like** sous une fiche pour la sauvegarder.\n• Clique sur **[Clique ici]** pour l'ouvrir sur Spotify.\n• *Pour obtenir un point au Top, tu dois écouter au moins 96% d'un morceau !*",
        inline=False
    )
    embed.add_field(
        name="📊 Dashboard complet :",
        value=f"[Clique ici pour voir toutes les statistiques en détail]({DASHBOARD_URL})",
        inline=False
    )
    return embed

async def verifier_et_mettre_a_jour_aide():
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return
    config = charger_config()
    msg_aide_id = config.get("message_aide_id")
    embed_aide = generer_embed_aide()
    message_existe = False
    if msg_aide_id:
        try:
            msg_existant = await salon.fetch_message(msg_aide_id)
            await msg_existant.edit(embed=embed_aide)
            message_existe = True
        except Exception: pass
    if not message_existe:
        nouveau_msg = await salon.send(embed=embed_aide)
        try: await nouveau_msg.pin()
        except Exception: pass
        config["message_aide_id"] = nouveau_msg.id
        sauvegarder_config(config)


couleur_cache = {}  

def obtenir_couleur_album(url_image, track_id=None):
    if track_id and track_id in couleur_cache:
        return couleur_cache[track_id]
    try:
        reponse = requests.get(url_image, timeout=10)
        img_bytes = io.BytesIO(reponse.content)
        color_thief = ColorThief(img_bytes)
        rgb = color_thief.get_color(quality=8)
        couleur = discord.Color.from_rgb(rgb[0], rgb[1], rgb[2])
    except Exception:
        couleur = discord.Color.green()

    if track_id:
        couleur_cache[track_id] = couleur
    return couleur

def generer_barre_progression(creation_time, duration):
    now = datetime.datetime.now(datetime.timezone.utc)
    temps_ecoule = (now - creation_time).total_seconds()
    durée_totale = duration.total_seconds()
    if temps_ecoule > durée_totale: temps_ecoule = durée_totale
    taille_barre = 10
    position_piste = int((temps_ecoule / durée_totale) * taille_barre) if durée_totale > 0 else 0
    barre = ""
    for i in range(taille_barre):
        if i == position_piste: barre += "🔘"
        else: barre += "▬"
    return f"{barre} `{int(temps_ecoule // 60)}:{int(temps_ecoule % 60):02d} / {int(durée_totale // 60)}:{int(durée_totale % 60):02d}`"


async def verifier_presence_spotify(membre):
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return

    user_id = str(membre.id)
    maintenant_timestamp = datetime.datetime.now(PARIS_TZ).timestamp()

    if user_id in verrous_anti_spam:
        if maintenant_timestamp - verrous_anti_spam[user_id] < 2:
            return
    verrous_anti_spam[user_id] = maintenant_timestamp

    spotify_activity = None
    for activity in membre.activities:
        if isinstance(activity, discord.Spotify):
            spotify_activity = activity
            break

    if spotify_activity:
        deja_en_cours = user_id in ecoutes_en_cours and ecoutes_en_cours[user_id]["track_id"] == spotify_activity.track_id

        if not deja_en_cours:
            couleur = await asyncio.to_thread(obtenir_couleur_album, spotify_activity.album_cover_url, spotify_activity.track_id)

            embed = discord.Embed(
                title=f"🎵 {membre.display_name} écoute :",
                description=f"**Titre :** {spotify_activity.title}\n**Artiste :** {spotify_activity.artist}\n**Album :** {spotify_activity.album}",
                color=couleur
            )
            embed.set_thumbnail(url=spotify_activity.album_cover_url)
            
            barre = generer_barre_progression(spotify_activity.start, spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)

            if user_id in ecoutes_en_cours:
                infos_anciennes = ecoutes_en_cours[user_id]
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                temps_ecoule = (now_utc - infos_anciennes["start_time"]).total_seconds()
                
                mettre_a_jour_historique_fin(membre, infos_anciennes["track_id"], temps_ecoule, infos_anciennes["duration"])

                try:
                    ancien_msg = infos_anciennes.get("message_obj") or await salon.fetch_message(infos_anciennes["message_id"])
                    await ancien_msg.delete()
                except Exception: pass

            await asyncio.to_thread(ajouter_a_l_historique, membre, spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.track_id)

            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url)
            message = await salon.send(embed=embed, view=view)
            
            ecoutes_en_cours[user_id] = {
                "message_id": message.id,
                "message_obj": message,
                "start_time": spotify_activity.start,
                "track_id": spotify_activity.track_id,
                "duration": spotify_activity.duration.total_seconds(),
                "activity": spotify_activity,
                "couleur": couleur
            }

    elif user_id in ecoutes_en_cours:
        infos = ecoutes_en_cours[user_id]
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        temps_ecoule = (now_utc - infos["start_time"]).total_seconds()
        duree_totale = infos["duration"]

        mettre_a_jour_historique_fin(membre, infos["track_id"], temps_ecoule, duree_totale)

        try:
            msg_a_supprimer = infos.get("message_obj") or await salon.fetch_message(infos["message_id"])
            await msg_a_supprimer.delete()
        except Exception: pass
        finally:
            if user_id in ecoutes_en_cours: del ecoutes_en_cours[user_id]


class LikeView(discord.ui.View):
    def __init__(self, titre, artiste, url):
        super().__init__(timeout=None)
        self.titre = titre
        self.artiste = artiste
        self.url = url

    @discord.ui.button(label="Like", style=discord.ButtonStyle.danger, emoji="🤍")
    async def bouton_like(self, interaction: discord.Interaction, button: discord.ui.Button):
        est_like = enregistrer_like_membre(interaction.user, self.titre, self.artiste, self.url)
        if est_like:
            await interaction.response.send_message(f"❤️ Ajouté à tes titres likés : **{self.titre}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"💔 Retiré de tes titres likés : **{self.titre}**", ephemeral=True)


@bot.event
async def on_ready():
    print(f"SpotBot est en ligne : {bot.user.name}")
    
    try:
        print("🔄 Synchronisation forcée des commandes slash avec Discord...")
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commandes slash synchronisées avec succès !")
    except Exception as e: 
        print(f"Erreur sync des commandes slash : {e}")
    
    await verifier_et_mettre_a_jour_aide()
    
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    config = charger_config()
    msg_aide_id = config.get("message_aide_id")
    if salon:
        try:
            async for message in salon.history(limit=50):
                if message.author == bot.user and message.id != msg_aide_id and message.id != config.get("message_top_id") and message.embeds:
                    await message.delete()
                    await asyncio.sleep(0.2)
        except Exception as e: print(f"Erreur nettoyage initial : {e}")
        
    actualiser_messages.start()
    sauvegarde_periodique_github.start()
    classement_hebdomadaire_auto.start()

@bot.event
async def on_ready():
    print(f"SpotBot est en ligne : {bot.user.name}")
    
    # Force l'application de l'activité avec le bouton au démarrage
    await bot.change_presence(status=discord.Status.online, activity=bot.activity)

@bot.event
async def on_presence_update(before, after):
    await verifier_presence_spotify(after)

@tasks.loop(seconds=30)
async def actualiser_messages():
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return
    for user_id, infos in list(ecoutes_en_cours.items()):
        try:
            msg = infos.get("message_obj") or await salon.fetch_message(infos["message_id"])
            spotify_activity = infos["activity"]
            embed = discord.Embed(title=msg.embeds[0].title, description=msg.embeds[0].description, color=infos["couleur"])
            embed.set_thumbnail(url=spotify_activity.album_cover_url)
            barre = generer_barre_progression(infos["start_time"], spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)
            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url)
            await msg.edit(embed=embed, view=view)
        except Exception:
            if user_id in ecoutes_en_cours: del ecoutes_en_cours[user_id]


@bot.tree.command(name="top", description="Affiche le classement hebdomadaire actuel des auditeurs")
async def top_semaine(interaction: discord.Interaction):
    stats = charger_stats()
    if not stats:
        await interaction.response.send_message("Aucune musique enregistrée cette semaine ! 🎧", ephemeral=True)
        return
    classement = sorted(stats.items(), key=lambda item: item[1]["count"], reverse=True)
    embed = discord.Embed(title="🏆 Classement Actuel de la Semaine", color=discord.Color.gold(), timestamp=datetime.datetime.now(PARIS_TZ))
    texte = ""
    for index, (u_id, data) in enumerate(classement[:10], start=1):
        nom = data.get("display_name", data.get("username", "Inconnu"))
        medailles = {1: "🥇", 2: "🥈", 3: "🥉"}
        texte += f"{medailles.get(index, f'`#{index}`')} **{nom}** — {data['count']} morceaux validés\n"
    embed.description = texte
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="likes", description="Affiche la liste de tes morceaux likés")
async def voir_likes(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    likes = charger_likes()
    if user_id not in likes or len(likes[user_id]["liste"]) == 0:
        await interaction.response.send_message("🤍 Tu n'as pas encore liké de morceaux !", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"❤️ Titres likés par {interaction.user.display_name}", color=discord.Color.red(), timestamp=datetime.datetime.now(PARIS_TZ))
    texte = ""
    for index, track in enumerate(likes[user_id]["liste"][-15:], start=1):
        texte += f"`{index}.` [{track['titre']}]({track['url']}) — *{track['artiste']}*\n"
    embed.description = texte
    embed.set_footer(text=f"Total : {len(likes[user_id]['liste'])} morceaux favoris")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="history", description="Affiche l'historique d'écoute par pages de 10 morceaux")
@app_commands.describe(
    page="Le numéro de la page à afficher (Ex: 1, 2, 3...)",
    membre="Le membre Discord (@Nom) dont tu veux voir l'historique (Optionnel)"
)
async def voir_historique(interaction: discord.Interaction, page: int = 1, membre: discord.Member = None):
    if page < 1:
        page = 1

    cible_membre = membre if membre else interaction.user
    user_id = str(cible_membre.id)
    
    historique = charger_historique()
    
    if user_id not in historique or len(historique[user_id]["ecoutes"]) == 0:
        nom_affiche = "Tu n'" if cible_membre == interaction.user else f"**{cible_membre.display_name}** n'"
        await interaction.response.send_message(f"🕒 {nom_affiche}as pas encore d'historique d'écoute enregistré.", ephemeral=True)
        return
        
    liste_totale = historique[user_id]["ecoutes"]
    total_elements = len(liste_totale)
    
    elements_par_page = 10
    index_debut = (page - 1) * elements_par_page
    index_fin = index_debut + elements_par_page
    
    morceaux_page = liste_totale[index_debut:index_fin]
    
    if not morceaux_page:
        await interaction.response.send_message(f"📂 La page `{page}` n'existe pas pour cet utilisateur (Total : {total_elements} écoutes).", ephemeral=True)
        return

    total_pages = (total_elements + elements_par_page - 1) // elements_par_page

    embed = discord.Embed(
        title=f"🕒 Historique d'écoute — {cible_membre.display_name}", 
        color=discord.Color.blue(), 
        timestamp=datetime.datetime.now(PARIS_TZ)
    )
    
    texte = ""
    for index, track in enumerate(morceaux_page, start=index_debut + 1):
        status = track.get('status', 'En cours...')
        texte += f"`{index}.` `[{track['date']}]` [{track['titre']}]({track['url']}) — *{track['artiste']}*\n╰─ {status}\n\n"
        
    embed.description = texte
    embed.set_footer(text=f"Page {page}/{total_pages} • Total : {total_elements} écoutes")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(DISCORD_TOKEN)