import os
import io
import re
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

# Empêche Git de tomber sur un prompt interactif
os.environ["GIT_TERMINAL_PROMPT"] = "0"

# Définition globale du fuseau horaire de Paris
PARIS_TZ = zoneinfo.ZoneInfo("Europe/Paris")

# Configuration du Bot Discord
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True

# Définition de l'activité
activite_profil = discord.Activity(
    type=discord.ActivityType.playing,
    name="SpotBot Dashboard",
    buttons=[
        {
            "label": "Aller sur le Dashboard", 
            "url": "https://naloulii.github.io/SpotBot"
        }
    ]
)

bot = commands.Bot(command_prefix="!", intents=intents, activity=activite_profil)

# ==========================================
#          CONFIGURATION SÉCURISÉE
# ==========================================
DASHBOARD_URL = "https://naloulii.github.io/SpotBot"
OWNER_ID = 566899759013429259  # Ton ID Discord (naloulii)[cite: 2, 3]

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME")
# ==========================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

ecoutes_en_cours = {}   
verrous_anti_spam = {}  

GITHUB_REPO_URL = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}.git"
git_dir = os.path.join(BASE_DIR, ".git")

def _adopter_dossier_comme_repo():
    r = Repo.init(BASE_DIR)
    if "origin" in [rem.name for rem in r.remotes]:
        r.remotes.origin.set_url(GITHUB_REPO_URL)
    else:
        r.create_remote("origin", GITHUB_REPO_URL)
    r.remotes.origin.fetch()
    r.git.symbolic_ref("HEAD", "refs/heads/main")
    r.git.reset("--mixed", "origin/main")
    r.git.branch("--set-upstream-to=origin/main", "main")
    return r

def _repo_est_sain(r):
    try:
        return (
            r.head.is_valid()
            and r.active_branch.name == "main"
            and r.active_branch.tracking_branch() is not None
        )
    except Exception:
        return False

if os.path.isdir(git_dir):
    repo = Repo(BASE_DIR)
    if "origin" in [rem.name for rem in repo.remotes]:
        repo.remotes.origin.set_url(GITHUB_REPO_URL)
    else:
        repo.create_remote("origin", GITHUB_REPO_URL)

    if _repo_est_sain(repo):
        print("📌 Dépôt Git détecté et sain à la racine du projet.")
    else:
        print("⚠️ Dépôt Git présent mais incomplet/invalide, réparation...")
        repo = _adopter_dossier_comme_repo()
else:
    print("🚀 Aucun dépôt Git existant ici : adoption du dossier comme dépôt Git...")
    repo = _adopter_dossier_comme_repo()

def _sauvegarde_github_bloquante():
    try:
        fichiers_a_ajouter = []
        for root, dirs, files in os.walk(DATA_DIR):
            if ".git" in root.split(os.sep):
                continue
            for file in files:
                if file.endswith(".json"):
                    rel_path = os.path.relpath(os.path.join(root, file), BASE_DIR)
                    fichiers_a_ajouter.append(rel_path)

        nb_fichiers_commites = 0
        if fichiers_a_ajouter:
            repo.index.add(fichiers_a_ajouter)
            if repo.is_dirty() or not repo.head.is_valid():
                maintenant = datetime.datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
                repo.index.commit(f"🤖 Auto-Save : Synchronisation des données ({maintenant})")
                nb_fichiers_commites = len(fichiers_a_ajouter)

        repo.remotes.origin.pull(rebase=True)

        if nb_fichiers_commites:
            repo.remotes.origin.push()
            print(f"📦 [GitHub] Données synchronisées avec succès : {nb_fichiers_commites} fichier(s)")
            return True, nb_fichiers_commites
    except Exception as e:
        print(f"⚠️ [GitHub] Erreur de synchronisation automatique : {e}")
        return False, str(e)
    return True, 0

@tasks.loop(minutes=15)
async def sauvegarde_periodique_github():
    await asyncio.to_thread(_sauvegarde_github_bloquante)

# ==========================================
#     GESTION DES DOSSIERS PAR SERVEUR
# ==========================================
ARTISTS_FILE = os.path.join(DATA_DIR, "artists.json")
_chemins_guildes = {}

def nettoyer_nom_dossier(nom):
    nettoye = re.sub(r"[^\w\-]+", "_", nom, flags=re.UNICODE).strip("_")
    if not nettoye:
        nettoye = "serveur"
    return nettoye[:50]

def resoudre_dossier_guilde(guild, forcer=False):
    guild_id = str(guild.id)
    if not forcer and guild_id in _chemins_guildes:
        return _chemins_guildes[guild_id]

    nom_voulu = f"{nettoyer_nom_dossier(guild.name)}_{guild_id}"
    chemin_voulu = os.path.join(DATA_DIR, nom_voulu)

    dossier_existant = None
    if os.path.isdir(DATA_DIR):
        for entree in os.listdir(DATA_DIR):
            if entree == ".git":
                continue
            chemin_entree = os.path.join(DATA_DIR, entree)
            if os.path.isdir(chemin_entree) and entree.endswith(f"_{guild_id}"):
                dossier_existant = chemin_entree
                break

    if dossier_existant and dossier_existant != chemin_voulu:
        try:
            os.rename(dossier_existant, chemin_voulu)
            print(f"📁 Dossier renommé : {os.path.basename(dossier_existant)} → {nom_voulu}")
        except Exception as e:
            print(f"⚠️ Impossible de renommer le dossier du serveur {guild.name} : {e}")
            chemin_voulu = dossier_existant
    else:
        os.makedirs(chemin_voulu, exist_ok=True)

    _chemins_guildes[guild_id] = chemin_voulu
    return chemin_voulu

def chemin_dossier_guilde(guild_id):
    if guild_id in _chemins_guildes:
        return _chemins_guildes[guild_id]
    dossier = os.path.join(DATA_DIR, str(guild_id))
    os.makedirs(dossier, exist_ok=True)
    return dossier

def chemin_fichier_guilde(guild_id, nom_fichier):
    return os.path.join(chemin_dossier_guilde(guild_id), nom_fichier)

def assurer_dossier_guilde(guild):
    guild_id = str(guild.id)
    dossier = resoudre_dossier_guilde(guild)
    config_path = os.path.join(dossier, "config.json")
    nouveau = not os.path.exists(config_path)

    if nouveau:
        sauvegarder_config(guild_id, {
            "salon_musique_id": None,
            "message_aide_id": None,
            "message_top_id": None
        })

    try:
        info_path = os.path.join(dossier, "guild_info.json")
        with open(info_path, "w") as f:
            json.dump({
                "id": guild_id,
                "name": guild.name,
                "icon_url": str(guild.icon.url) if guild.icon else None
            }, f, indent=4)
    except Exception:
        pass

    return nouveau

# Chargeurs & Sauvegardes
def charger_stats(guild_id):
    try:
        with open(chemin_fichier_guilde(guild_id, "stats.json"), "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_stats(guild_id, stats):
    with open(chemin_fichier_guilde(guild_id, "stats.json"), "w") as f: json.dump(stats, f, indent=4)

def charger_likes(guild_id):
    try:
        with open(chemin_fichier_guilde(guild_id, "likes.json"), "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_likes(guild_id, likes):
    with open(chemin_fichier_guilde(guild_id, "likes.json"), "w") as f: json.dump(likes, f, indent=4)

def charger_config(guild_id):
    try:
        with open(chemin_fichier_guilde(guild_id, "config.json"), "r") as f: return json.load(f)
    except FileNotFoundError: return {"salon_musique_id": None, "message_aide_id": None, "message_top_id": None}

def sauvegarder_config(guild_id, config):
    with open(chemin_fichier_guilde(guild_id, "config.json"), "w") as f: json.dump(config, f, indent=4)

def charger_historique(guild_id):
    try:
        with open(chemin_fichier_guilde(guild_id, "historique.json"), "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_historique(guild_id, historique):
    with open(chemin_fichier_guilde(guild_id, "historique.json"), "w") as f: json.dump(historique, f, indent=4)

def charger_artistes_cache():
    try:
        with open(ARTISTS_FILE, "r") as f: return json.load(f)
    except FileNotFoundError: return {}

def sauvegarder_artistes_cache(cache):
    with open(ARTISTS_FILE, "w") as f: json.dump(cache, f, indent=4)

# ==========================================
#          RECHERCHE D'IMAGES API
# ==========================================
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
    return None

def rechercher_cover_track(titre, artiste):
    """Recherche la pochette d'un morceau spécifique sur Deezer si elle est manquante."""
    try:
        reponse = requests.get(
            "https://api.deezer.com/search/track",
            params={"q": f"{titre} {artiste}", "limit": 1},
            timeout=10
        )
        reponse.raise_for_status()
        data = reponse.json()
        items = data.get("data", [])
        if items:
            return items[0].get("album", {}).get("cover_medium")
    except Exception as e:
        print(f"⚠️ [Deezer] Erreur cover pour '{titre}' : {e}")
    return None

def mettre_a_jour_cache_artistes(chaine_artistes):
    if not chaine_artistes: return
    noms = [a.strip() for a in chaine_artistes.split(";") if a.strip()]
    if not noms: return

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

# ==========================================
#         ALGORITHMES DE STATS/LIKES
# ==========================================
def enregistrer_stat_membre(guild_id, membre):
    user_id = str(membre.id)
    stats = charger_stats(guild_id)
    if user_id not in stats:
        stats[user_id] = {"username": membre.name, "display_name": membre.display_name, "avatar_url": str(membre.display_avatar.url), "count": 0}
    stats[user_id]["username"] = membre.name
    stats[user_id]["display_name"] = membre.display_name
    stats[user_id]["avatar_url"] = str(membre.display_avatar.url)
    stats[user_id]["count"] += 1
    sauvegarder_stats(guild_id, stats)

def enregistrer_like_membre(guild_id, membre, titre, artiste, url, cover_url=None):
    user_id = str(membre.id)
    likes = charger_likes(guild_id)
    if user_id not in likes:
        likes[user_id] = {"username": membre.name, "display_name": membre.display_name, "avatar_url": str(membre.display_avatar.url), "liste": []}
    likes[user_id]["username"] = membre.name
    likes[user_id]["display_name"] = membre.display_name
    likes[user_id]["avatar_url"] = str(membre.display_avatar.url)
    
    deja_like = any(track['url'] == url for track in likes[user_id]["liste"])
    if deja_like:
        likes[user_id]["liste"] = [t for t in likes[user_id]["liste"] if t['url'] != url]
        sauvegarder_likes(guild_id, likes)
        return False
    else:
        if not cover_url:
            cover_url = rechercher_cover_track(titre, artiste)
        likes[user_id]["liste"].append({"titre": titre, "artiste": artiste, "url": url, "cover_url": cover_url})
        sauvegarder_likes(guild_id, likes)
        return True

def finaliser_ecoutes_orphelines(guild_id, membre, historique):
    user_id = str(membre.id)
    if user_id not in historique: return
    orphelines_reparees = 0
    for ecoute in historique[user_id]["ecoutes"]:
        if ecoute["status"] == "En cours...":
            ecoute["status"] = "🎉 Écouté en entier"
            orphelines_reparees += 1
    if orphelines_reparees:
        for _ in range(orphelines_reparees):
            enregistrer_stat_membre(guild_id, membre)

def ajouter_a_l_historique(guild_id, membre, titre, artiste, url, track_id, cover_url=None):
    user_id = str(membre.id)
    historique = charger_historique(guild_id)
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

    finaliser_ecoutes_orphelines(guild_id, membre, historique)

    if not cover_url:
        cover_url = rechercher_cover_track(titre, artiste)

    maintenant = datetime.datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    historique[user_id]["ecoutes"].insert(0, {
        "date": maintenant, 
        "titre": titre, 
        "artiste": artiste, 
        "url": url, 
        "track_id": track_id,
        "cover_url": cover_url,
        "status": "En cours..."
    })
    historique[user_id]["ecoutes"] = historique[user_id]["ecoutes"][:100]
    sauvegarder_historique(guild_id, historique)
    mettre_a_jour_cache_artistes(artiste)

def mettre_a_jour_historique_fin(guild_id, membre, track_id, temps_ecoule, duree_totale):
    user_id = str(membre.id)
    historique = charger_historique(guild_id)
    if user_id not in historique: return

    ecoutes = historique[user_id]["ecoutes"]
    index_actuel = None
    for i, ecoute in enumerate(ecoutes):
        if ecoute.get("track_id") == track_id and ecoute["status"] == "En cours...":
            index_actuel = i
            break
    if index_actuel is None: return

    ecoute_actuelle = ecoutes[index_actuel]
    ecoute_actuelle["temps_ecoute_secondes"] = round(temps_ecoule, 1)

    temps_cumule = temps_ecoule
    for ecoute_precedente in ecoutes[index_actuel + 1:]:
        if ecoute_precedente.get("track_id") != track_id: continue
        if ecoute_precedente["status"] == "🎉 Écouté en entier": break
        temps_cumule += ecoute_precedente.get("temps_ecoute_secondes", 0)

    valide = duree_totale > 0 and (temps_cumule / duree_totale) >= 0.96
    if valide:
        ecoute_actuelle["status"] = "🎉 Écouté en entier"
    else:
        m, s = divmod(int(temps_ecoule), 60)
        ecoute_actuelle["status"] = f"⏱️ Écouté pendant {m}m {s:02d}s"

    sauvegarder_historique(guild_id, historique)
    if valide:
        enregistrer_stat_membre(guild_id, membre)

# ==========================================
#      VÉRIFICATION & CORRECTION DES COVERS
# ==========================================
def verifier_et_reparer_covers_globales():
    """Parcourt l'ensemble des fichiers historique.json et likes.json de chaque serveur.[cite: 2, 3]
    Si un morceau n'a pas de cover_url, il interroge Deezer, met à jour le fichier localement
    et retourne True s'il y a eu des modifications."""
    print("🔍 [Covers] Début de la vérification globale des cover_url manquants...")
    modifie = False

    if not os.path.exists(DATA_DIR):
        return False

    for item in os.listdir(DATA_DIR):
        chemin_item = os.path.join(DATA_DIR, item)
        if not os.path.isdir(chemin_item) or item == ".git":
            continue

        # 1. Traitement historique.json[cite: 2]
        historique_path = os.path.join(chemin_item, "historique.json")
        if os.path.exists(historique_path):
            try:
                with open(historique_path, "r", encoding="utf-8") as f:
                    historique = json.load(f)
                historique_change = False
                for user_id, user_data in historique.items():
                    for ecoute in user_data.get("ecoutes", []):
                        if not ecoute.get("cover_url"):
                            print(f"🖼️ [Covers] Cover manquante détectée pour '{ecoute['titre']}' - {ecoute['artiste']}")
                            nouvelle_cover = rechercher_cover_track(ecoute['titre'], ecoute['artiste'])
                            if nouvelle_cover:
                                ecoute["cover_url"] = nouvelle_cover
                                historique_change = True
                                modifie = True
                                time.sleep(0.5) 
                if historique_change:
                    with open(historique_path, "w", encoding="utf-8") as f:
                        json.dump(historique, f, indent=4)
            except Exception as e:
                print(f"⚠️ [Covers] Erreur historique.json pour {item} : {e}")

        # 2. Traitement likes.json[cite: 3]
        likes_path = os.path.join(chemin_item, "likes.json")
        if os.path.exists(likes_path):
            try:
                with open(likes_path, "r", encoding="utf-8") as f:
                    likes = json.load(f)
                likes_change = False
                for user_id, user_data in likes.items():
                    for track in user_data.get("liste", []):
                        if not track.get("cover_url"):
                            print(f"🖼️ [Covers] Cover manquante détectée dans Likes pour '{track['titre']}' - {track['artiste']}")
                            nouvelle_cover = rechercher_cover_track(track['titre'], track['artiste'])
                            if nouvelle_cover:
                                track["cover_url"] = nouvelle_cover
                                likes_change = True
                                modifie = True
                                time.sleep(0.5)
                if likes_change:
                    with open(likes_path, "w", encoding="utf-8") as f:
                        json.dump(likes, f, indent=4)
            except Exception as e:
                print(f"⚠️ [Covers] Erreur likes.json pour {item} : {e}")

    print("🏁 [Covers] Vérification globale terminée.")
    return modifie

# ==========================================
#            UI & AFFICHAGE
# ==========================================
def construire_embed_classement(stats, titre="🏆 Classement de la Semaine Dernière"):
    embed = discord.Embed(title=titre, color=discord.Color.gold(), timestamp=datetime.datetime.now(PARIS_TZ))
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
    return embed

def trouver_derniere_archive_top(guild_id):
    dossier = chemin_dossier_guilde(guild_id)
    meilleure = None
    try:
        entrees = os.listdir(dossier)
    except FileNotFoundError: return None

    for nom_fichier in entrees:
        correspondance = re.match(r"^stats_week_(\d+)_(\d+)\.json$", nom_fichier)
        if not correspondance: continue
        semaine = int(correspondance.group(1))
        annee = int(correspondance.group(2))
        chemin = os.path.join(dossier, nom_fichier)
        if meilleure is None or (annee, semaine) > (meilleure[0], meilleure[1]):
            meilleure = (annee, semaine, chemin)

    if meilleure is None: return None
    annee, semaine, chemin = meilleure
    try:
        with open(chemin, "r") as f: return json.load(f), semaine, annee
    except Exception: return None

# Tâche Hebdomadaire
@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=PARIS_TZ))
async def classement_hebdomadaire_auto():
    if datetime.datetime.now(PARIS_TZ).weekday() != 0: return

    for guild in bot.guilds:
        guild_id = str(guild.id)
        config = charger_config(guild_id)
        salon_id = config.get("salon_musique_id")
        if not salon_id: continue
        salon = bot.get_channel(salon_id)
        if not salon: continue

        stats = charger_stats(guild_id)
        embed = construire_embed_classement(stats)

        msg_top_id = config.get("message_top_id")
        message_existe = False
        if msg_top_id:
            try:
                msg_existant = await salon.fetch_message(msg_top_id)
                await msg_existant.edit(embed=embed)
                message_existe = True
            except Exception: pass
        if not message_existe:
            try:
                nouveau_msg = await salon.send(embed=embed)
                config["message_top_id"] = nouveau_msg.id
                sauvegarder_config(guild_id, config)
            except Exception: pass

        num_semaine = datetime.datetime.now(PARIS_TZ).strftime("%V_%Y")
        archive_file = chemin_fichier_guilde(guild_id, f"stats_week_{num_semaine}.json")
        with open(archive_file, "w") as f: json.dump(stats, f, indent=4)
        sauvegarder_stats(guild_id, {})

    await asyncio.to_thread(_sauvegarde_github_bloquante)

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

async def verifier_et_mettre_a_jour_aide(guild_id):
    config = charger_config(guild_id)
    salon_id = config.get("salon_musique_id")
    if not salon_id: return None
    salon = bot.get_channel(salon_id)
    if not salon: return None

    msg_aide_id = config.get("message_aide_id")
    embed_aide = generer_embed_aide()
    message_existe = False
    
    if msg_aide_id:
        try:
            msg_existant = await salon.fetch_message(msg_aide_id)
            await msg_existant.edit(embed=embed_aide)
            message_existe = True
            return msg_existant.id
        except Exception: pass
        
    if not message_existe:
        try:
            nouveau_msg = await salon.send(embed=embed_aide)
            try: await nouveau_msg.pin()
            except Exception: pass
            config["message_aide_id"] = nouveau_msg.id
            sauvegarder_config(guild_id, config)
            return nouveau_msg.id
        except Exception: pass
    return None

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

# ==========================================
#        VÉRIFICATION STATUT DE L'ÉCOUTE
# ==========================================
async def verifier_presence_spotify(membre):
    if membre.guild is None: return
    guild_id = str(membre.guild.id)
    config = charger_config(guild_id)
    salon_id = config.get("salon_musique_id")
    if not salon_id: return 

    salon = bot.get_channel(salon_id)
    if not salon: return

    user_id = str(membre.id)
    cle = (guild_id, user_id)
    maintenant_timestamp = datetime.datetime.now(PARIS_TZ).timestamp()

    if cle in verrous_anti_spam:
        if maintenant_timestamp - verrous_anti_spam[cle] < 2: return
    verrous_anti_spam[cle] = maintenant_timestamp

    spotify_activity = None
    for activity in membre.activities:
        if isinstance(activity, discord.Spotify):
            spotify_activity = activity
            break

    if spotify_activity:
        deja_en_cours = cle in ecoutes_en_cours and ecoutes_en_cours[cle]["track_id"] == spotify_activity.track_id

        if not deja_en_cours:
            cover_url = spotify_activity.album_cover_url
            if not cover_url:
                cover_url = await asyncio.to_thread(rechercher_cover_track, spotify_activity.title, spotify_activity.artist)

            couleur = await asyncio.to_thread(obtenir_couleur_album, cover_url, spotify_activity.track_id) if cover_url else discord.Color.green()

            embed = discord.Embed(
                title=f"🎵 {membre.display_name} écoute :",
                description=f"**Titre :** {spotify_activity.title}\n**Artiste :** {spotify_activity.artist}\n**Album :** {spotify_activity.album}",
                color=couleur
            )
            if cover_url:
                embed.set_thumbnail(url=cover_url)
            
            barre = generer_barre_progression(spotify_activity.start, spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)

            if cle in ecoutes_en_cours:
                infos_anciennes = ecoutes_en_cours[cle]
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                temps_ecoule = (now_utc - infos_anciennes["start_time"]).total_seconds()
                
                mettre_a_jour_historique_fin(guild_id, membre, infos_anciennes["track_id"], temps_ecoule, infos_anciennes["duration"])

                try:
                    ancien_msg = infos_anciennes.get("message_obj") or await salon.fetch_message(infos_anciennes["message_id"])
                    await ancien_msg.delete()
                except Exception: pass

            await asyncio.to_thread(ajouter_a_l_historique, guild_id, membre, spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.track_id, cover_url)

            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, cover_url)
            message = await salon.send(embed=embed, view=view)
            
            ecoutes_en_cours[cle] = {
                "message_id": message.id,
                "message_obj": message,
                "salon_id": salon_id,
                "start_time": spotify_activity.start,
                "track_id": spotify_activity.track_id,
                "duration": spotify_activity.duration.total_seconds(),
                "activity": spotify_activity,
                "couleur": couleur,
                "cover_url": cover_url
            }

    elif cle in ecoutes_en_cours:
        infos = ecoutes_en_cours[cle]
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        temps_ecoule = (now_utc - infos["start_time"]).total_seconds()
        duree_totale = infos["duration"]

        mettre_a_jour_historique_fin(guild_id, membre, infos["track_id"], temps_ecoule, duree_totale)

        try:
            msg_a_supprimer = infos.get("message_obj") or await salon.fetch_message(infos["message_id"])
            await msg_a_supprimer.delete()
        except Exception: pass
        finally:
            if cle in ecoutes_en_cours: del ecoutes_en_cours[cle]


class LikeView(discord.ui.View):
    def __init__(self, titre, artiste, url, cover_url=None):
        super().__init__(timeout=None)
        self.titre = titre
        self.artiste = artiste
        self.url = url
        self.cover_url = cover_url

    @discord.ui.button(label="Like", style=discord.ButtonStyle.danger, emoji="🤍")
    async def bouton_like(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None:
            await interaction.response.send_message("Cette action doit être faite depuis un serveur.", ephemeral=True)
            return
        guild_id = str(interaction.guild_id)
        
        if not self.cover_url:
            self.cover_url = rechercher_cover_track(self.titre, self.artiste)
            
        est_like = enregistrer_like_membre(guild_id, interaction.user, self.titre, self.artiste, self.url, self.cover_url)
        if est_like:
            await interaction.response.send_message(f"❤️ Ajouté à tes titres likés : **{self.titre}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"💔 Retiré de tes titres likés : **{self.titre}**", ephemeral=True)


async def finaliser_ecoutes_perimees(guild):
    guild_id = str(guild.id)
    historique = charger_historique(guild_id)
    if not historique: return

    modifie = False
    for user_id, data in historique.items():
        ecoutes = data.get("ecoutes", [])
        if not ecoutes or ecoutes[0].get("status") != "En cours...": continue

        cle = (guild_id, user_id)
        if cle in ecoutes_en_cours: continue  

        membre = guild.get_member(int(user_id))
        track_id_actuel = None
        if membre:
            for activity in membre.activities:
                if isinstance(activity, discord.Spotify):
                    track_id_actuel = activity.track_id
                    break

        if track_id_actuel is not None and track_id_actuel == ecoutes[0].get("track_id"):
            continue  

        ecoutes[0]["status"] = "🎉 Écouté en entier"
        modifie = True
        if membre:
            enregistrer_stat_membre(guild_id, membre)

    if modifie:
        sauvegarder_historique(guild_id, historique)


@tasks.loop(minutes=5)
async def verifier_ecoutes_perimees():
    for guild in bot.guilds:
        try:
            await finaliser_ecoutes_perimees(guild)
        except Exception as e:
            print(f"⚠️ [{guild.name}] Erreur vérification écoutes : {e}")

# ==========================================
#          MESSAGES PRIVÉS (TICKETS)
# ==========================================
class TicketCloseView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Fermer le ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def bouton_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("🚫 Seul Naloulii peut fermer ce ticket.", ephemeral=True)[cite: 2, 3]
            return

        await interaction.response.defer()

        try:
            user = await bot.fetch_user(self.user_id)
            if user:
                embed_ferme = discord.Embed(
                    title="🔒 Ticket résolu",
                    description="Votre ticket a été marqué comme résolu et fermé par notre administrateur. N'hésitez pas à renvoyer un message si vous avez une autre question !",
                    color=discord.Color.red()
                )
                await user.send(embed=embed_ferme)
        except Exception as e:
            print(f"Impossible de notifier l'utilisateur de la fermeture : {e}")

        await interaction.channel.delete(reason="Ticket fermé par l'administrateur.")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # --- CAS 1 : L'UTILISATEUR ÉCRIT EN MP AU BOT (Ouverture / Envoi dans le salon) ---
    if isinstance(message.channel, discord.DMChannel):
        guilde = None
        for g in bot.guilds:
            if g.get_member(OWNER_ID) is not None:[cite: 2, 3]
                guilde = g
                break

        if not guilde:
            await message.channel.send("❌ Erreur interne : Impossible de trouver le serveur d'administration.")
            return

        categorie_privée = discord.utils.get(guilde.categories, name="privé") or discord.utils.get(guilde.categories, name="🔒 privé")
        if not categorie_privée:
            await message.channel.send("❌ Erreur : La catégorie de salon privé 'privé' n'existe pas sur le serveur d'administration.")
            return

        nom_salon = f"ticket-{clean_channel_name(message.author.name)}"
        salon_ticket = discord.utils.get(categorie_privée.text_channels, name=nom_salon)

        if not salon_ticket:
            overwrites = {
                guilde.default_role: discord.PermissionOverwrite(read_messages=False),
                guilde.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True),
                guilde.get_member(OWNER_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True)[cite: 2, 3]
            }
            
            salon_ticket = await guilde.create_text_channel(
                name=nom_salon,
                category=categorie_privée,
                overwrites=overwrites,
                topic=f"Ticket de {message.author.display_name} ({message.author.id})"
            )

            embed_init = discord.Embed(
                title=f"📩 Nouveau ticket de {message.author.display_name}",
                description=f"Les messages que tu écris ici seront envoyés directement en MP à **{message.author.mention}**.\nLe bouton ci-dessous fermera le ticket et supprimera ce salon.",
                color=discord.Color.blurple()
            )
            embed_init.set_thumbnail(url=message.author.display_avatar.url)
            embed_init.add_field(name="Pseudo complet", value=f"`{message.author.name}`", inline=True)
            embed_init.add_field(name="ID Utilisateur", value=f"`{message.author.id}`", inline=True)

            view = TicketCloseView(message.author.id)
            await salon_ticket.send(embed=embed_init, view=view)

        embed_msg = discord.Embed(
            description=message.content,
            color=discord.Color.light_grey(),
            timestamp=datetime.datetime.now(PARIS_TZ)
        )
        embed_msg.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

        fichiers = []
        if message.attachments:
            for attachment in message.attachments:
                fichiers.append(await attachment.to_file())

        await salon_ticket.send(embed=embed_msg, files=fichiers)

        try:
            await message.add_reaction("✅")
        except Exception:
            pass

    # --- CAS 2 : TU ÉCRIS DANS UN SALON DE TICKET (Transmission en MP à l'utilisateur) ---
    elif message.channel.category and (message.channel.category.name.lower() == "privé" or message.channel.category.name == "🔒 privé"):
        if message.channel.name.startswith("ticket-"):
            topic = message.channel.topic
            if topic:
                match = re.search(r"\((\d+)\)", topic)
                if match:
                    user_id = int(match.group(1))
                    try:
                        user = await bot.fetch_user(user_id)
                        if user:
                            embed_reply = discord.Embed(
                                description=message.content,
                                color=discord.Color.green(),
                                timestamp=datetime.datetime.now(PARIS_TZ)
                            )
                            embed_reply.set_author(name="Naloulii (Admin)", icon_url=message.author.display_avatar.url)[cite: 2, 3]

                            fichiers = []
                            if message.attachments:
                                for attachment in message.attachments:
                                    fichiers.append(await attachment.to_file())

                            await user.send(embed=embed_reply, files=fichiers)
                            await message.add_reaction("📤")
                    except Exception as e:
                        await message.channel.send(f"❌ Impossible d'envoyer le message en MP à l'utilisateur : {e}")

    await bot.process_commands(message)

def clean_channel_name(name):
    """Nettoie le nom d'un utilisateur pour qu'il soit compatible avec les règles de nommage des salons Discord."""
    name = name.lower()
    name = re.sub(r"[^\w\-]+", "-", name, flags=re.UNICODE)
    return name.strip("-")


# ==========================================
#             INITIALISATION DU BOT
# ==========================================
@bot.event
async def on_ready():
    print(f"SpotBot est en ligne : {bot.user.name}")

    try:
        print("🔄 Synchronisation forcée des commandes slash avec Discord...")
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commandes slash synchronisées avec succès !")
    except Exception as e:
        print(f"Erreur sync des commandes slash : {e}")

    # --- ÉTAPE 1 : CORRECTION ET PUSH AUTOMATIQUE DES COVERS MANQUANTES AU DÉMARRAGE ---
    loop = asyncio.get_running_loop()
    besoin_push = await loop.run_in_executor(None, verifier_et_reparer_covers_globales)
    if besoin_push:
        print("📦 [Covers] Des covers manquantes ont été réparées, envoi immédiat vers GitHub...")
        await loop.run_in_executor(None, _sauvegarde_github_bloquante)
    else:
        print("✅ [Covers] Tous les fichiers JSON possèdent déjà leurs covers d'albums.")

    # --- ÉTAPE 2 : CHARGEMENT DES CONFIGURATIONS SANS SUPPRIMER LES BIENVENUS/MESSAGES ÉPINGLÉS ---
    for guild in bot.guilds:
        assurer_dossier_guilde(guild)
        guild_id = str(guild.id)

        try:
            await finaliser_ecoutes_perimees(guild)
        except Exception as e:
            print(f"⚠️ [{guild.name}] Erreur réparation écoutes bloquées : {e}")

        config = charger_config(guild_id)
        salon_id = config.get("salon_musique_id")
        if not salon_id: continue  

        # On met d'abord à jour et on récupère les ID réels des messages
        msg_aide_reel_id = await verifier_et_mettre_a_jour_aide(guild_id)
        
        config = charger_config(guild_id)
        msg_top_reel_id = config.get("message_top_id")

        salon = bot.get_channel(salon_id)

        if salon and not msg_top_reel_id:
            archive = trouver_derniere_archive_top(guild_id)
            if archive:
                stats_archive, semaine, annee = archive
                embed_archive = construire_embed_classement(
                    stats_archive,
                    titre=f"🏆 Classement de la Semaine {semaine} ({annee})"
                )
                try:
                    # CORRECTION APPORTÉE ICI : Ajout du paramètre nommé 'embed=' pour éviter le texte brut
                    nouveau_msg = await salon.send(embed=embed_archive)  
                    config["message_top_id"] = nouveau_msg.id
                    sauvegarder_config(guild_id, config)
                    msg_top_reel_id = nouveau_msg.id
                except Exception as e:
                    print(f"⚠️ [{guild.name}] Impossible de publier le classement : {e}")

        if salon:
            try:
                async for message in salon.history(limit=50):
                    if message.author == bot.user:
                        if message.id == msg_aide_reel_id or message.id == msg_top_reel_id or message.pinned:
                            continue
                        if message.embeds:
                            await message.delete()
                            await asyncio.sleep(0.2)
            except Exception as e:
                print(f"Erreur nettoyage initial ({guild.name}) : {e}")

    await bot.change_presence(status=discord.Status.online, activity=bot.activity)

    actualiser_messages.start()
    sauvegarde_periodique_github.start()
    classement_hebdomadaire_auto.start()
    verifier_ecoutes_perimees.start()


@bot.event
async def on_guild_update(before, after):
    if before.name != after.name:
        resoudre_dossier_guilde(after, forcer=True)
        try:
            info_path = os.path.join(chemin_dossier_guilde(str(after.id)), "guild_info.json")
            with open(info_path, "w") as f:
                json.dump({
                    "id": str(after.id),
                    "name": after.name,
                    "icon_url": str(after.icon.url) if after.icon else None
                }, f, indent=4)
        except Exception: pass
        await asyncio.to_thread(_sauvegarde_github_bloquante)


@bot.event
async def on_guild_join(guild):
    print(f"➕ SpotBot a été ajouté au serveur : {guild.name} ({guild.id})")
    assurer_dossier_guilde(guild)

    owner = guild.owner
    if owner:
        embed_dm = discord.Embed(
            title="🎵 Merci d'avoir ajouté SpotBot ! 🤖",
            description=(
                f"Bonjour **{owner.display_name}** !\n\n"
                f"Pour démarrer sur **{guild.name}**, tu dois choisir "
                "le salon où seront publiées les activités musicales en temps réel avec la commande :\n\n"
                "👉 **/setup salon:#votre-salon**\n\n"
                "*(Il est fortement recommandé de créer un salon textuel vide dédié, par exemple `#spotbot` ou `#musique`)*"
            ),
            color=discord.Color.from_rgb(30, 215, 96)
        )
        embed_dm.add_field(
            name="📩 Système Intégré de Support & Tickets :",
            value=(
                "• **Support direct par MP** : Tes membres peuvent envoyer un message privé (DM) au Bot à tout moment !\n"
                "• **Retransmission automatique** : Le message sera instantanément transmis de manière sécurisée et "
                "propre sous forme de ticket à mon créateur (**naloulii**)[cite: 2, 3].\n"
                "• **Pas d'encombrement** : Aucun canal d'administration ou configuration additionnelle n'est requis !"
            ),
            inline=False
        )
        embed_dm.add_field(
            name="🔒 Permissions requises pour le bot dans ce salon :",
            value=(
                "• **Voir le salon**\n"
                "• **Envoyer des messages**\n"
                "• **Intégrer des liens** (requis pour l'affichage des fiches)\n"
                "• **Épingler des messages** (requis pour le message d'aide)"
            ),
            inline=False
        )
        try:
            await owner.send(embed=embed_dm)
            print(f"📬 Message de configuration envoyé en DM au propriétaire : {owner.name}")
        except Exception as e:
            print(f"⚠️ Impossible d'envoyer le DM d'explication au propriétaire : {e}")

    await asyncio.to_thread(_sauvegarde_github_bloquante)


@bot.event
async def on_presence_update(before, after):
    await verifier_presence_spotify(after)

@tasks.loop(seconds=30)
async def actualiser_messages():
    for cle, infos in list(ecoutes_en_cours.items()):
        salon = bot.get_channel(infos["salon_id"])
        if not salon:
            del ecoutes_en_cours[cle]
            continue
        try:
            msg = infos.get("message_obj") or await salon.fetch_message(infos["message_id"])
            spotify_activity = infos["activity"]
            
            cover_url = infos.get("cover_url")
            if not cover_url:
                cover_url = await asyncio.to_thread(rechercher_cover_track, spotify_activity.title, spotify_activity.artist)

            embed = discord.Embed(title=msg.embeds[0].title, description=msg.embeds[0].description, color=infos["couleur"])
            if cover_url:
                embed.set_thumbnail(url=cover_url)
            barre = generer_barre_progression(infos["start_time"], spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)
            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, cover_url)
            await msg.edit(embed=embed, view=view)
        except Exception:
            if cle in ecoutes_en_cours: del ecoutes_en_cours[cle]

# ==========================================
#                COMMANDES
# ==========================================
@bot.tree.command(name="setup", description="(Admins) Choisir le salon où SpotBot publie l'activité musicale")
@app_commands.describe(salon="Le salon textuel où SpotBot postera l'activité musicale")
@app_commands.guild_only()
async def setup_config(interaction: discord.Interaction, salon: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "🚫 Il faut la permission **Gérer le serveur** pour configurer SpotBot.", ephemeral=True
        )
        return

    permissions_bot = salon.permissions_for(interaction.guild.me)
    if not (permissions_bot.view_channel and permissions_bot.send_messages and permissions_bot.embed_links):
        await interaction.response.send_message(
            f"⚠️ Je n'ai pas assez de permissions dans {salon.mention} (il me faut : voir le salon, "
            f"envoyer des messages et intégrer des liens). Ajuste mes permissions puis relance /setup.",
            ephemeral=True
        )
        return

    guild_id = str(interaction.guild.id)
    assurer_dossier_guilde(interaction.guild)
    config = charger_config(guild_id)
    config["salon_musique_id"] = salon.id
    sauvegarder_config(guild_id, config)

    await interaction.response.send_message(f"✅ Salon configuré : {salon.mention} — c'est prêt !", ephemeral=True)

    await verifier_et_mettre_a_jour_aide(guild_id)
    await asyncio.to_thread(_sauvegarde_github_bloquante)


@bot.tree.command(name="top", description="Affiche le classement hebdomadaire actuel des auditeurs")
@app_commands.guild_only()
async def top_semaine(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    stats = charger_stats(guild_id)
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
@app_commands.guild_only()
async def voir_likes(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    likes = charger_likes(guild_id)
    if user_id not in likes or len(likes[user_id]["liste"]) == 0:
        await interaction.response.send_message("🤍 Tu n'as pas encore liké de morceaux !", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"❤️ Titres likés par {interaction.user.display_name}", color=discord.Color.red(), timestamp=datetime.datetime.now(PARIS_TZ))
    texte = ""
    dernier_like_avec_cover = None

    for index, track in enumerate(likes[user_id]["liste"][-15:], start=1):
        cover = track.get("cover_url")
        if not cover:
            cover = rechercher_cover_track(track['titre'], track['artiste'])
            track["cover_url"] = cover
            sauvegarder_likes(guild_id, likes)

        if cover:
            dernier_like_avec_cover = cover

        texte += f"`{index}.` [{track['titre']}]({track['url']}) — *{track['artiste']}*\n"
    
    embed.description = texte
    if dernier_like_avec_cover:
        embed.set_thumbnail(url=dernier_like_avec_cover)

    embed.set_footer(text=f"Total : {len(likes[user_id]['liste'])} morceaux favoris")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="history", description="Affiche l'historique d'écoute par pages de 10 morceaux")
@app_commands.describe(
    page="Le numéro de la page à afficher (Ex: 1, 2, 3...)",
    membre="Le membre Discord (@Nom) dont tu veux voir l'historique (Optionnel)"
)
@app_commands.guild_only()
async def voir_historique(interaction: discord.Interaction, page: int = 1, membre: discord.Member = None):
    if page < 1: page = 1
    guild_id = str(interaction.guild.id)
    cible_membre = membre if membre else interaction.user
    user_id = str(cible_membre.id)
    
    historique = charger_historique(guild_id)
    
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
    embed = discord.Embed(title=f"🕒 Historique d'écoute — {cible_membre.display_name}", color=discord.Color.blue(), timestamp=datetime.datetime.now(PARIS_TZ))
    
    texte = ""
    derniere_cover_trouvee = None

    for index, track in enumerate(morceaux_page, start=index_debut + 1):
        status = track.get('status', 'En cours...')
        cover = track.get("cover_url")
        
        if not cover:
            cover = rechercher_cover_track(track['titre'], track['artiste'])
            track["cover_url"] = cover
            sauvegarder_historique(guild_id, historique)

        if cover:
            derniere_cover_trouvee = cover

        texte += f"`{index}.` `[{track['date']}]` [{track['titre']}]({track['url']}) — *{track['artiste']}*\n╰─ {status}\n\n"
        
    embed.description = texte
    if len(morceaux_page) > 0 and derniere_cover_trouvee:
         embed.set_thumbnail(url=derniere_cover_trouvee)
        
    embed.set_footer(text=f"Page {page}/{total_pages} • Total : {total_elements} écoutes")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==========================================
#      COMMANDE MANUELLE SYNC GITHUB
# ==========================================
@bot.tree.command(name="git-sync", description="Force la synchronisation des bases de données locales vers GitHub (Réservé à Naloulii)")
@app_commands.guild_only()
async def manual_git_sync(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("🚫 Cette commande est ultra-sécurisée et réservée à mon créateur (**naloulii**).", ephemeral=True)[cite: 2, 3]
        return

    await interaction.response.defer(ephemeral=True)

    loop = asyncio.get_running_loop()
    succes, resultat = await loop.run_in_executor(None, _sauvegarde_github_bloquante)

    if succes:
        await interaction.followup.send(f"✅ **Synchronisation GitHub exécutée avec succès !**\n📦 {resultat} fichier(s) JSON mis à jour sur ton dépôt.")
    else:
        await interaction.followup.send(f"❌ **Erreur de synchronisation :**\n`{resultat}`")


@bot.command(name="gitsync")
async def manual_git_sync_text(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("🚫 Cette commande est ultra-sécurisée et réservée à mon créateur (**naloulii**).")[cite: 2, 3]
        return

    msg = await ctx.send("🔄 Synchronisation GitHub en cours...")
    loop = asyncio.get_running_loop()
    succes, resultat = await loop.run_in_executor(None, _sauvegarde_github_bloquante)

    if succes:
        await msg.edit(content=f"✅ **Synchronisation GitHub exécutée avec succès !**\n📦 {resultat} fichier(s) JSON mis à jour sur ton dépôt.")
    else:
        await msg.edit(content=f"❌ **Erreur de synchronisation :**\n`{resultat}`")


bot.run(DISCORD_TOKEN)