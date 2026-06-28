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

# Dictionnaire pour suivre l'état des écoutes en cours sur Discord
ecoutes_en_cours = {}

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
        # On récupère les éventuels changements distants pour éviter les conflits
        repo.remotes.origin.pull()
        
        fichiers_data = ["stats.json", "likes.json", "config.json", "historique.json"]
        fichiers_a_ajouter = []
        
        for f in fichiers_data:
            if os.path.exists(os.path.join(DATA_DIR, f)):
                fichiers_a_ajouter.append(f)
                
        if fichiers_a_ajouter:
            # On ajoute les fichiers relativement au sous-dossier géré par Git
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
    except FileNotFoundError: return {"message_aide_id": None}

def sauvegarder_config(config):
    with open(CONFIG_FILE, "w") as f: json.dump(config, f, indent=4)

def charger_historique():
    try:
        with open(HISTORIQUE_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_historique(historique):
    with open(HISTORIQUE_FILE, "w") as f: json.dump(historique, f, indent=4)


# Fonctions d'écriture avec stockage structuré (ID + Pseudo)
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

def ajouter_a_l_historique(membre, titre, artiste, url):
    user_id = str(membre.id)
    historique = charger_historique()
    if user_id not in historique:
        historique[user_id] = {"username": membre.name, "display_name": membre.display_name, "ecoutes": []}
    historique[user_id]["username"] = membre.name
    historique[user_id]["display_name"] = membre.display_name
    
    maintenant = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    historique[user_id]["ecoutes"].insert(0, {"date": maintenant, "titre": titre, "artiste": artiste, "url": url})
    historique[user_id]["ecoutes"] = historique[user_id]["ecoutes"][:100]
    sauvegarder_historique(historique)


# Gestion de l'Embed de Guide unique automatique
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
        value="• Clique sur le bouton **🤍 Like** sous une fiche pour la sauvegarder.\n• Clique sur **[Clique ici]** pour l'ouvrir sur Spotify.",
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


# Outils Graphiques (Couleurs d'albums & Barre)
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


# Fonction globale d'analyse d'activité Spotify
async def verifier_presence_spotify(membre):
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    if not salon: return

    spotify_activity = None
    for activity in membre.activities:
        if isinstance(activity, discord.Spotify):
            spotify_activity = activity
            break

    user_id = str(membre.id)

    if spotify_activity:
        deja_en_cours = user_id in ecoutes_en_cours and ecoutes_en_cours[user_id]["track_id"] == spotify_activity.track_id

        if not deja_en_cours:
            enregistrer_stat_membre(membre)
            ajouter_a_l_historique(membre, spotify_activity.title, spotify_activity.artist, spotify_activity.track_url)
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

            if user_id in ecoutes_en_cours:
                try:
                    ancien_msg = await salon.fetch_message(ecoutes_en_cours[user_id]["message_id"])
                    await ancien_msg.delete()
                except Exception: pass

            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url)
            message = await salon.send(embed=embed, view=view)
            
            ecoutes_en_cours[user_id] = {
                "message_id": message.id,
                "start_time": spotify_activity.start,
                "track_id": spotify_activity.track_id,
                "activity": spotify_activity,
                "couleur": couleur
            }

    elif user_id in ecoutes_en_cours:
        try:
            message_id = ecoutes_en_cours[user_id]["message_id"]
            msg_a_supprimer = await salon.fetch_message(message_id)
            await msg_a_supprimer.delete()
        except Exception: pass
        finally: del ecoutes_en_cours[user_id]


# Composant du Bouton de Like classique des fiches du salon
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
    
    # Nettoyage des fiches bloquées au démarrage
    salon = bot.get_channel(SALON_MUSIQUE_ID)
    config = charger_config()
    msg_aide_id = config.get("message_aide_id")
    if salon:
        try:
            async for message in salon.history(limit=50):
                if message.author == bot.user and message.id != msg_aide_id and message.embeds:
                    await message.delete()
                    await asyncio.sleep(0.2)
        except Exception as e: print(f"Erreur nettoyage initial : {e}")
        
    # Scan global des écoutes déjà en cours sur tous les serveurs au démarrage
    print("🔍 Scan des écoutes déjà en cours...")
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot:
                await verifier_presence_spotify(member)
    print("✅ Scan terminé et statuts synchronisés.")
        
    actualiser_messages.start()
    sauvegarde_periodique_github.start()

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


# Commandes App / Commandes Slash éphémères (Retour aux versions textuelles stables)
@bot.tree.command(name="top", description="Affiche le classement hebdomadaire des auditeurs")
async def top_semaine(interaction: discord.Interaction):
    stats = charger_stats()
    if not stats:
        await interaction.response.send_message("Aucune musique enregistrée cette semaine ! 🎧", ephemeral=True)
        return
    classement = sorted(stats.items(), key=lambda item: item[1]["count"], reverse=True)
    embed = discord.Embed(title="🏆 Classement Hebdomadaire des Auditeurs", color=discord.Color.gold(), timestamp=datetime.datetime.now())
    texte = ""
    for index, (u_id, data) in enumerate(classement[:10], start=1):
        nom = data.get("display_name", data.get("username", "Inconnu"))
        medailles = {1: "🥇", 2: "🥈", 3: "🥉"}
        texte += f"{medailles.get(index, f'`#{index}`')} **{nom}** — {data['count']} morceaux écoutés\n"
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
        texte += f"`{track['date']}` : [{track['titre']}]({track['url']}) — *{track['artiste']}*\n"
    embed.description = texte
    embed.set_footer(text=f"Affichage des 10 dernières écoutes (Total : {len(historique[user_id]['ecoutes'])})")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Lancement sécurisé du bot
bot.run(DISCORD_TOKEN)