import os
import io
import json
import asyncio
import datetime
import requests
import discord
from discord.ext import commands, tasks
from colorthief import ColorThief
from git import Repo

# Configuration du Bot Discord
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
#          CONFIGURATION SÉCURISÉE
# ==========================================
SALON_MUSIQUE_ID = 1520393495544594472 

# Récupération des jetons secrets via l'hébergeur Cloud
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME")
# ==========================================

# Configuration des chemins locaux dans le conteneur Docker
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

STATS_FILE = os.path.join(DATA_DIR, "stats.json")
LIKES_FILE = os.path.join(DATA_DIR, "likes.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORIQUE_FILE = os.path.join(DATA_DIR, "historique.json")

# Dictionnaires de suivi de l'état global
ecoutes_en_cours = {}
verrous_anti_spam = {} # Empêche les doubles déclenchements simultanés

# Connexion / Clonage automatique dans le sous-dossier sécurisé 'data'
GITHUB_REPO_URL = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}.git"

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

# --- FONCTION DE SAUVEGARDE GITHUB (Toutes les 15 minutes) ---
@tasks.loop(minutes=15)
async def sauvegarde_periodique_github():
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
            maintenant = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            repo.index.commit(f"🤖 Auto-Save : Synchronisation des données ({maintenant})")
            repo.remotes.origin.push()
            print(f"📦 [GitHub] Données synchronisées avec succès : {fichiers_a_ajouter}")
    except Exception as e:
        print(f"⚠️ [GitHub] Erreur de synchronisation automatique : {e}")

# Fonctions basiques de gestion de données locales (JSON)
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


def enregistrer_stat_membre(membre):
    user_id = str(membre.id)
    stats = charger_stats()
    if user_id not in stats:
        stats[user_id] = {"username": membre.name, "display_name": membre.display_name, "count": 0}
    stats[user_id]["username"] = membre.name
    stats[user_id]["display_name"] = membre.display_name
    stats[user_id]["count"] += 1
    sauvegarder_stats(stats)

def enregistrer_like_membre(membre, titre, artiste, url):
    user_id = str(membre.id)
    likes = charger_likes()
    if user_id not in likes:
        likes[user_id] = {"username": membre.name, "display_name": membre.display_name, "liste": []}
    likes[user_id]["username"] = membre.name
    likes[user_id]["display_name"] = membre.display_name
    
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
        historique[user_id] = {"username": membre.name, "display_name": membre.display_name, "ecoutes": []}
    historique[user_id]["username"] = membre.name
    historique[user_id]["display_name"] = membre.display_name
    
    maintenant = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
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

def mettre_a_jour_historique_fin(membre, track_id, temps_ecoule, duree_totale):
    user_id = str(membre.id)
    historique = charger_historique()
    if user_id in historique:
        for ecoute in historique[user_id]["ecoutes"]:
            if ecoute.get("track_id") == track_id and ecoute["status"] == "En cours...":
                if temps_ecoule >= (duree_totale * 0.98):
                    ecoute["status"] = "Écouté en entier"
                else:
                    m, s = divmod(int(temps_ecoule), 60)
                    ecoute["status"] = f"Écouté pendant {m}:{s:02d}"
                break
        sauvegarder_historique(historique)


# --- TASK : TOUS LES LUNDIS 00:00 ---
@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
async def classement_hebdomadaire_auto():
    if datetime.datetime.now().weekday() != 0:
        return

    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return

    stats = charger_stats()
    config = charger_config()
    
    embed = discord.Embed(
        title="🏆 Classement de la Semaine Dernière", 
        color=discord.Color.gold(), 
        timestamp=datetime.datetime.now()
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

    num_semaine = datetime.datetime.now().strftime("%V_%Y")
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
        timestamp=datetime.datetime.now()
    )
    embed.add_field(
        name="📚 Commandes disponibles :",
        value=(
            "**/top** : Classement hebdomadaire des plus grands auditeurs. 🏆\n"
            "**/likes** : La liste complète de tes morceaux favoris. ❤️\n"
            "**/history** : Ton historique des 10 dernières écoutes. 🕒"
        ),
        inline=False
    )
    embed.add_field(
        name="⭐ Fonctionnalités :",
        value="• Clique sur le bouton **🤍 Like** sous une fiche pour la sauvegarder.\n• Clique sur **[Clique ici]** pour l'ouvrir sur Spotify.\n• *Pour obtenir un point au Top, tu dois écouter au moins 90% d'un morceau !*",
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


def obtenir_couleur_album(url_image):
    try:
        reponse = requests.get(url_image)
        img_bytes = io.BytesIO(reponse.content)
        color_thief = ColorThief(img_bytes)
        rgb = color_thief.get_color(quality=1)
        return discord.Color.from_rgb(rgb[0], rgb[1], rgb[2])
    except Exception: return discord.Color.green()

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


# --- ANALYSE DE L'ACTIVITÉ (CORRIGÉE CONTRE LES DOUBLONS SUR LES SERVERS PARTAGÉS) ---
async def verifier_presence_spotify(membre):
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return

    user_id = str(membre.id)
    maintenant_timestamp = datetime.datetime.now().timestamp()

    # ANTI-SPAM SYSTEM : Ignore les requêtes identiques espacées de moins de 3 secondes pour cet ID
    if user_id in verrous_anti_spam:
        if maintenant_timestamp - verrous_anti_spam[user_id] < 3:
            return
    verrous_anti_spam[user_id] = maintenant_timestamp

    spotify_activity = None
    for activity in membre.activities:
        if isinstance(activity, discord.Spotify):
            spotify_activity = activity
            break

    if spotify_activity:
        # Est-ce exactement le même morceau déjà enregistré dans notre salon live ?
        deja_en_cours = user_id in ecoutes_en_cours and ecoutes_en_cours[user_id]["track_id"] == spotify_activity.track_id

        if not deja_en_cours:
            couleur = obtenir_couleur_album(spotify_activity.album_cover_url)

            embed = discord.Embed(
                title=f"🎵 {membre.display_name} écoute :",
                description=f"**Titre :** {spotify_activity.title}\n**Artiste :** {spotify_activity.artist}\n**Album :** {spotify_activity.album}",
                color=couleur
            )
            embed.set_thumbnail(url=spotify_activity.album_cover_url)
            
            barre = generer_barre_progression(spotify_activity.start, spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)

            # Si l'utilisateur change de musique d'un coup, on ferme l'ancienne proprement
            if user_id in ecoutes_en_cours:
                infos_anciennes = ecoutes_en_cours[user_id]
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                temps_ecoule = (now_utc - infos_anciennes["start_time"]).total_seconds()
                
                if infos_anciennes["duration"] > 0 and (temps_ecoule / infos_anciennes["duration"]) >= 0.90:
                    enregistrer_stat_membre(membre)
                
                mettre_a_jour_historique_fin(membre, infos_anciennes["track_id"], temps_ecoule, infos_anciennes["duration"])

                try:
                    ancien_msg = await salon.fetch_message(infos_anciennes["message_id"])
                    await ancien_msg.delete()
                except Exception: pass

            # Ajout unique dans l'historique au début de l'écoute
            ajouter_a_l_historique(membre, spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.track_id)

            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url)
            message = await salon.send(embed=embed, view=view)
            
            ecoutes_en_cours[user_id] = {
                "message_id": message.id,
                "start_time": spotify_activity.start,
                "track_id": spotify_activity.track_id,
                "duration": spotify_activity.duration.total_seconds(),
                "activity": spotify_activity,
                "couleur": couleur
            }

    elif user_id in ecoutes_en_cours:
        # L'écoute s'est arrêtée
        infos = ecoutes_en_cours[user_id]
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        temps_ecoule = (now_utc - infos["start_time"]).total_seconds()
        duree_totale = infos["duration"]

        # Sauvegarde du temps réel dans l'historique
        mettre_a_jour_historique_fin(membre, infos["track_id"], temps_ecoule, duree_totale)

        if duree_totale > 0 and (temps_ecoule / duree_totale) >= 0.90:
            enregistrer_stat_membre(membre)

        try:
            msg_a_supprimer = await salon.fetch_message(infos["message_id"])
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
    try: await bot.tree.sync()
    except Exception as e: print(f"Erreur sync des commandes slash : {e}")
    
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
async def on_presence_update(before, after):
    await verifier_presence_spotify(after)

@tasks.loop(seconds=15)
async def actualiser_messages():
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return
    for user_id, infos in list(ecoutes_en_cours.items()):
        try:
            msg = await salon.fetch_message(infos["message_id"])
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


# Commandes App / Commandes Slash éphémères
@bot.tree.command(name="top", description="Affiche le classement hebdomadaire actuel des auditeurs")
async def top_semaine(interaction: discord.Interaction):
    stats = charger_stats()
    if not stats:
        await interaction.response.send_message("Aucune musique enregistrée cette semaine ! 🎧", ephemeral=True)
        return
    classement = sorted(stats.items(), key=lambda item: item[1]["count"], reverse=True)
    embed = discord.Embed(title="🏆 Classement Actuel de la Semaine", color=discord.Color.gold(), timestamp=datetime.datetime.now())
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
    
    embed = discord.Embed(title=f"❤️ Titres likés par {interaction.user.display_name}", color=discord.Color.red(), timestamp=datetime.datetime.now())
    texte = ""
    for index, track in enumerate(likes[user_id]["liste"][-15:], start=1):
        texte += f"`{index}.` [{track['titre']}]({track['url']}) — *{track['artiste']}*\n"
    embed.description = texte
    embed.set_footer(text=f"Total : {len(likes[user_id]['liste'])} morceaux favoris")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="history", description="Affiche l'historique de tes écoutes récentes")
async def voir_historique(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    historique = charger_historique()
    if user_id not in historique or len(historique[user_id]["ecoutes"]) == 0:
        await interaction.response.send_message("🕒 Tu n'as pas encore d'historique d'écoute enregistré.", ephemeral=True)
        return
    embed = discord.Embed(title=f"🕒 Historique d'écoute de {interaction.user.display_name}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
    texte = ""
    for index, track in enumerate(historique[user_id]["ecoutes"][:10], start=1):
        # Affiche proprement le statut de temps (ex: "Écouté en entier" ou "Écouté pendant 1:24")
        status = track.get('status', 'En cours...')
        texte += f"`{track['date']}` : [{track['titre']}]({track['url']}) — *{track['artiste']}* (`{status}`)\n"
    embed.description = texte
    embed.set_footer(text=f"Affichage des 10 dernières écoutes (Total : {len(historique[user_id]['ecoutes'])})")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(DISCORD_TOKEN)