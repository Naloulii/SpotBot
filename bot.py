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
    name="SpotBot Dashboard",
    buttons=[
        {
            "label": "Aller sur le Dashboard", 
            "url": "https://naloulii.github.io/SpotBot-data/"
        }
    ]
)

bot = commands.Bot(command_prefix="!", intents=intents, activity=activite_profil)

# ==========================================
#          CONFIGURATION SÉCURISÉE
# ==========================================
DASHBOARD_URL = "https://naloulii.github.io/SpotBot-data/"

# Récupération des jetons secrets via l'hébergeur Cloud (Railway)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_REPO_NAME = os.getenv("GITHUB_REPO_NAME")
# ==========================================

# Configuration des chemins locaux dans le conteneur Docker
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR est un simple sous-dossier DANS le même dépôt que bot.py (pas un clone
# séparé) : il ne contient que les données (dossiers de serveur + artists.json).
# bot.py, requirements.txt, index.html restent à la racine du dépôt.
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

ecoutes_en_cours = {}   # clé = (guild_id, user_id)
verrous_anti_spam = {}  # clé = (guild_id, user_id)

GITHUB_REPO_URL = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}.git"

# Le dépôt Git est celui de BASE_DIR tout entier (bot.py + data/), pas un clone
# séparé dans un sous-dossier. Si l'hébergeur a déjà déployé via un vrai
# git clone (donc .git présent), on l'utilise tel quel. Sinon, on adopte le
# dossier existant en place (git init + remote + reset sur origin/main) sans
# jamais dupliquer bot.py à l'intérieur de data/.
git_dir = os.path.join(BASE_DIR, ".git")

def _adopter_dossier_comme_repo():
    """Initialise/répare le dépôt Git sur le dossier existant SANS toucher aux
    fichiers déjà présents. `checkout` touche le working tree et plante si des
    fichiers non trackés portent déjà le nom de fichiers présents sur
    origin/main ; `reset --mixed` ne touche que l'index (la "liste de suivi"),
    jamais les fichiers sur disque, donc pas de plantage possible ici."""
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
    """Vérifie que le dépôt a bien une branche 'main' locale valide avec un
    suivi vers origin/main configuré. Si ce n'est pas le cas (ex: reliquat
    d'un précédent démarrage qui a planté avant de finir son initialisation),
    le dépôt doit être réparé plutôt que réutilisé tel quel."""
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

# --- FONCTION DE SAUVEGARDE GITHUB (Toutes les 15 minutes, + à la demande) ---
def _sauvegarde_github_bloquante():
    try:
        fichiers_a_ajouter = []
        for root, dirs, files in os.walk(DATA_DIR):
            if ".git" in root.split(os.sep):
                continue
            for file in files:
                if file.endswith(".json"):
                    # Chemin relatif à la RACINE du dépôt (BASE_DIR), pas à data/,
                    # car c'est ce que git add/commit attend (ex: "data/Serveur_id/stats.json")
                    rel_path = os.path.relpath(os.path.join(root, file), BASE_DIR)
                    fichiers_a_ajouter.append(rel_path)

        # On commit d'abord les changements locaux (s'il y en a) AVANT de pull,
        # sinon git refuse le pull dès qu'un fichier local a été modifié sans être
        # commité (ex: message_top_id sauvegardé dans config.json juste avant) :
        # "Your local changes... would be overwritten by merge".
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
    except Exception as e:
        print(f"⚠️ [GitHub] Erreur de synchronisation automatique : {e}")


@tasks.loop(minutes=15)
async def sauvegarde_periodique_github():
    await asyncio.to_thread(_sauvegarde_github_bloquante)


# ==========================================
#     GESTION DES DOSSIERS PAR SERVEUR
# ==========================================
# artists.json est partagé entre tous les serveurs (les artistes sont les mêmes
# partout), donc il vit à la RACINE du repo, pas dans un dossier de serveur.
ARTISTS_FILE = os.path.join(DATA_DIR, "artists.json")

# Cache en mémoire : guild_id (str) -> chemin absolu du dossier déjà résolu.
# Évite de re-scanner le disque à chaque appel (ces fonctions sont appelées très
# souvent, à chaque écoute Spotify).
_chemins_guildes = {}

def nettoyer_nom_dossier(nom):
    """Rend un nom de serveur Discord utilisable comme nom de dossier (retire
    emojis/accents spéciaux/caractères interdits, garde lettres/chiffres/tirets)."""
    nettoye = re.sub(r"[^\w\-]+", "_", nom, flags=re.UNICODE).strip("_")
    if not nettoye:
        nettoye = "serveur"
    return nettoye[:50]

def resoudre_dossier_guilde(guild, forcer=False):
    """Retourne le chemin du dossier de données pour ce serveur, au format
    'NomDuServeur_ID'. Si le serveur a été renommé depuis la dernière fois
    (un dossier '..._ID' existe déjà avec un ancien nom), le dossier est
    renommé sur place pour rester à jour, plutôt que d'en créer un nouveau."""
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
            print(f"📁 Dossier renommé (serveur renommé) : {os.path.basename(dossier_existant)} → {nom_voulu}")
        except Exception as e:
            print(f"⚠️ Impossible de renommer le dossier du serveur {guild.name} : {e}")
            chemin_voulu = dossier_existant
    else:
        os.makedirs(chemin_voulu, exist_ok=True)

    _chemins_guildes[guild_id] = chemin_voulu
    return chemin_voulu

def chemin_dossier_guilde(guild_id):
    """Lecture depuis le cache résolu par resoudre_dossier_guilde(). Ce cache est
    rempli pour tous les serveurs connus au démarrage (on_ready) et à l'arrivée
    sur un nouveau serveur (on_guild_join), donc toujours dispo en pratique."""
    if guild_id in _chemins_guildes:
        return _chemins_guildes[guild_id]
    # Filet de sécurité si jamais appelé avant résolution complète
    dossier = os.path.join(DATA_DIR, str(guild_id))
    os.makedirs(dossier, exist_ok=True)
    return dossier

def chemin_fichier_guilde(guild_id, nom_fichier):
    return os.path.join(chemin_dossier_guilde(guild_id), nom_fichier)

def assurer_dossier_guilde(guild):
    """Crée (si besoin) le dossier du serveur + un config.json par défaut + un guild_info.json
    utile pour le futur dashboard (nom / icône du serveur). Retourne True si le dossier vient
    d'être créé (nouveau serveur)."""
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


# Fonctions de gestion de données locales (JSON), maintenant par serveur
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
        likes[user_id]["liste"].append({"titre": titre, "artiste": artiste, "url": url, "cover_url": cover_url})
        sauvegarder_likes(guild_id, likes)
        return True

def finaliser_ecoutes_orphelines(guild_id, membre, historique):
    """Marque comme terminées (🎉) toutes les écoutes de ce membre restées
    bloquées sur 'En cours...' et leur attribue un point au classement.

    Ça arrive quand le bot redémarre (crash, redeploy...) pendant qu'un
    membre écoutait : le suivi en mémoire (ecoutes_en_cours) est perdu, donc
    ni le passage à la musique suivante ni l'arrêt de l'écoute ne déclenchent
    normalement mettre_a_jour_historique_fin, et l'entrée reste bloquée.
    On considère ces écoutes comme terminées dès qu'on constate qu'une
    musique plus récente a démarré pour ce membre (preuve que l'ancienne
    est bel et bien finie)."""
    user_id = str(membre.id)
    if user_id not in historique:
        return
    orphelines_reparees = 0
    for ecoute in historique[user_id]["ecoutes"]:
        if ecoute["status"] == "En cours...":
            ecoute["status"] = "🎉 Écouté en entier"
            orphelines_reparees += 1
    if orphelines_reparees:
        for _ in range(orphelines_reparees):
            enregistrer_stat_membre(guild_id, membre)
        print(f"🔧 [{membre.guild.name}] {orphelines_reparees} écoute(s) bloquée(s) sur 'En cours...' réparée(s) pour {membre.display_name}")


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

    # On s'apprête à insérer une nouvelle écoute "En cours..." : toute écoute
    # précédente encore marquée "En cours..." à ce stade est forcément une
    # orpheline (le flux normal l'aurait déjà finalisée via
    # mettre_a_jour_historique_fin avant d'appeler cette fonction).
    finaliser_ecoutes_orphelines(guild_id, membre, historique)

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

    sauvegarder_historique(guild_id, historique)

    if valide:
        enregistrer_stat_membre(guild_id, membre)


def construire_embed_classement(stats, titre="🏆 Classement de la Semaine Dernière"):
    """Construit l'embed de classement à partir d'un dict de stats (mêmes clés que
    stats.json / stats_week_WW_YYYY.json). Réutilisé par la tâche hebdomadaire
    et par la récupération d'archive au démarrage."""
    embed = discord.Embed(
        title=titre,
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
    return embed


def trouver_derniere_archive_top(guild_id):
    """Cherche dans le dossier du serveur les fichiers d'archive 'stats_week_WW_YYYY.json'
    et retourne (stats_dict, semaine, annee) pour l'archive la plus récente, ou None si
    aucune archive n'existe."""
    dossier = chemin_dossier_guilde(guild_id)
    meilleure = None  # (annee, semaine, chemin)

    try:
        entrees = os.listdir(dossier)
    except FileNotFoundError:
        return None

    for nom_fichier in entrees:
        correspondance = re.match(r"^stats_week_(\d+)_(\d+)\.json$", nom_fichier)
        if not correspondance:
            continue
        semaine = int(correspondance.group(1))
        annee = int(correspondance.group(2))
        chemin = os.path.join(dossier, nom_fichier)
        if meilleure is None or (annee, semaine) > (meilleure[0], meilleure[1]):
            meilleure = (annee, semaine, chemin)

    if meilleure is None:
        return None

    annee, semaine, chemin = meilleure
    try:
        with open(chemin, "r") as f:
            stats = json.load(f)
    except Exception:
        return None

    return stats, semaine, annee


# --- TASK : TOUS LES LUNDIS 00:00 (HEURE DE PARIS), POUR CHAQUE SERVEUR ---
@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=PARIS_TZ))
async def classement_hebdomadaire_auto():
    if datetime.datetime.now(PARIS_TZ).weekday() != 0:
        return

    for guild in bot.guilds:
        guild_id = str(guild.id)
        config = charger_config(guild_id)
        salon_id = config.get("salon_musique_id")
        if not salon_id:
            continue
        salon = bot.get_channel(salon_id)
        if not salon:
            continue

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
        with open(archive_file, "w") as f:
            json.dump(stats, f, indent=4)

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
            "**/history [page] [membre]** : Historique d'écoute (le tien ou celui d'un ami via son @). 🕒\n"
            "**/setup [salon]** : (Admins) Choisir ou changer le salon d'affichage. ⚙️"
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
    if not salon_id: return
    salon = bot.get_channel(salon_id)
    if not salon: return

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
        try:
            nouveau_msg = await salon.send(embed=embed_aide)
            try: await nouveau_msg.pin()
            except Exception: pass
            config["message_aide_id"] = nouveau_msg.id
            sauvegarder_config(guild_id, config)
        except Exception: pass


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
    if membre.guild is None:
        return
    guild_id = str(membre.guild.id)
    config = charger_config(guild_id)
    salon_id = config.get("salon_musique_id")
    if not salon_id:
        return  # Serveur pas encore configuré (le propriétaire doit faire /setup)

    salon = bot.get_channel(salon_id)
    if not salon: return

    user_id = str(membre.id)
    cle = (guild_id, user_id)
    maintenant_timestamp = datetime.datetime.now(PARIS_TZ).timestamp()

    if cle in verrous_anti_spam:
        if maintenant_timestamp - verrous_anti_spam[cle] < 2:
            return
    verrous_anti_spam[cle] = maintenant_timestamp

    spotify_activity = None
    for activity in membre.activities:
        if isinstance(activity, discord.Spotify):
            spotify_activity = activity
            break

    if spotify_activity:
        deja_en_cours = cle in ecoutes_en_cours and ecoutes_en_cours[cle]["track_id"] == spotify_activity.track_id

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

            if cle in ecoutes_en_cours:
                infos_anciennes = ecoutes_en_cours[cle]
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                temps_ecoule = (now_utc - infos_anciennes["start_time"]).total_seconds()
                
                mettre_a_jour_historique_fin(guild_id, membre, infos_anciennes["track_id"], temps_ecoule, infos_anciennes["duration"])

                try:
                    ancien_msg = infos_anciennes.get("message_obj") or await salon.fetch_message(infos_anciennes["message_id"])
                    await ancien_msg.delete()
                except Exception: pass

            await asyncio.to_thread(ajouter_a_l_historique, guild_id, membre, spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.track_id, spotify_activity.album_cover_url)

            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.album_cover_url)
            message = await salon.send(embed=embed, view=view)
            
            ecoutes_en_cours[cle] = {
                "message_id": message.id,
                "message_obj": message,
                "salon_id": salon_id,
                "start_time": spotify_activity.start,
                "track_id": spotify_activity.track_id,
                "duration": spotify_activity.duration.total_seconds(),
                "activity": spotify_activity,
                "couleur": couleur
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
        est_like = enregistrer_like_membre(guild_id, interaction.user, self.titre, self.artiste, self.url, self.cover_url)
        if est_like:
            await interaction.response.send_message(f"❤️ Ajouté à tes titres likés : **{self.titre}**", ephemeral=True)
        else:
            await interaction.response.send_message(f"💔 Retiré de tes titres likés : **{self.titre}**", ephemeral=True)


async def reparer_ecoutes_bloquees_au_demarrage(guild):
    """Au démarrage du bot, compare chaque écoute la plus récente encore
    marquée 'En cours...' avec la présence Spotify actuelle du membre :
    - si le membre écoute encore exactement ce morceau, on laisse
      verifier_presence_spotify reprendre le suivi normalement ;
    - sinon (autre morceau, ou plus d'écoute Spotify du tout), l'écoute est
      restée bloquée à cause d'un redémarrage/crash du bot pendant qu'elle
      était en cours : on la finalise et on ajoute le point au classement."""
    guild_id = str(guild.id)
    historique = charger_historique(guild_id)
    if not historique:
        return

    modifie = False
    for user_id, data in historique.items():
        ecoutes = data.get("ecoutes", [])
        if not ecoutes or ecoutes[0].get("status") != "En cours...":
            continue

        membre = guild.get_member(int(user_id))
        track_id_actuel = None
        if membre:
            for activity in membre.activities:
                if isinstance(activity, discord.Spotify):
                    track_id_actuel = activity.track_id
                    break

        if track_id_actuel is not None and track_id_actuel == ecoutes[0].get("track_id"):
            continue  # Toujours en train d'écouter ce même morceau, rien à réparer

        ecoutes[0]["status"] = "🎉 Écouté en entier"
        modifie = True
        if membre:
            enregistrer_stat_membre(guild_id, membre)
        print(f"🔧 [{guild.name}] Écoute bloquée sur 'En cours...' réparée pour {data.get('display_name', user_id)} (redémarrage du bot)")

    if modifie:
        sauvegarder_historique(guild_id, historique)


@bot.event
async def on_ready():
    print(f"SpotBot est en ligne : {bot.user.name}")

    try:
        print("🔄 Synchronisation forcée des commandes slash avec Discord...")
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commandes slash synchronisées avec succès !")
    except Exception as e:
        print(f"Erreur sync des commandes slash : {e}")

    # S'assure que chaque serveur où le bot est déjà présent a bien son dossier de données
    for guild in bot.guilds:
        assurer_dossier_guilde(guild)
        guild_id = str(guild.id)

        try:
            await reparer_ecoutes_bloquees_au_demarrage(guild)
        except Exception as e:
            print(f"⚠️ [{guild.name}] Erreur réparation écoutes bloquées : {e}")

        config = charger_config(guild_id)
        salon_id = config.get("salon_musique_id")
        if not salon_id:
            continue  # Serveur pas encore configuré, on ne touche à rien

        await verifier_et_mettre_a_jour_aide(guild_id)

        salon = bot.get_channel(salon_id)

        # Si aucun message de classement n'existe encore pour ce serveur mais qu'il y a
        # des archives hebdomadaires (stats_week_WW_YYYY.json), on publie la plus récente
        # au lieu d'attendre le prochain lundi minuit.
        if salon and not config.get("message_top_id"):
            archive = trouver_derniere_archive_top(guild_id)
            if archive:
                stats_archive, semaine, annee = archive
                embed_archive = construire_embed_classement(
                    stats_archive,
                    titre=f"🏆 Classement de la Semaine {semaine} ({annee})"
                )
                try:
                    nouveau_msg = await salon.send(embed=embed_archive)
                    config["message_top_id"] = nouveau_msg.id
                    sauvegarder_config(guild_id, config)
                    print(f"🏆 [{guild.name}] Message de classement recréé depuis l'archive stats_week_{semaine}_{annee}.json")
                except Exception as e:
                    print(f"⚠️ [{guild.name}] Impossible de publier le classement archivé : {e}")

        msg_aide_id = config.get("message_aide_id")
        if salon:
            try:
                async for message in salon.history(limit=50):
                    if message.author == bot.user and message.id != msg_aide_id and message.id != config.get("message_top_id") and message.embeds:
                        await message.delete()
                        await asyncio.sleep(0.2)
            except Exception as e:
                print(f"Erreur nettoyage initial ({guild.name}) : {e}")

    # Force l'application de l'activité avec le bouton au démarrage
    await bot.change_presence(status=discord.Status.online, activity=bot.activity)

    actualiser_messages.start()
    sauvegarde_periodique_github.start()
    classement_hebdomadaire_auto.start()


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
        except Exception:
            pass
        await asyncio.to_thread(_sauvegarde_github_bloquante)


@bot.event
async def on_guild_join(guild):
    print(f"➕ SpotBot a été ajouté au serveur : {guild.name} ({guild.id})")
    assurer_dossier_guilde(guild)

    # Message de bienvenue expliquant comment se configurer, dans le premier salon disponible
    salon_cible = guild.system_channel
    if salon_cible is None or not salon_cible.permissions_for(guild.me).send_messages:
        for c in guild.text_channels:
            if c.permissions_for(guild.me).send_messages:
                salon_cible = c
                break

    if salon_cible:
        embed = discord.Embed(
            title="🎵 Merci d'avoir ajouté SpotBot !",
            description=(
                "Pour démarrer, un membre avec la permission **Gérer le serveur** doit choisir "
                "le salon où seront publiées les activités musicales avec la commande :\n\n"
                "**/setup salon:#votre-salon**"
            ),
            color=discord.Color.from_rgb(30, 215, 96)
        )
        try:
            await salon_cible.send(embed=embed)
        except Exception:
            pass

    # Pousse immédiatement le nouveau dossier vers GitHub pour qu'il apparaisse tout de suite
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
            embed = discord.Embed(title=msg.embeds[0].title, description=msg.embeds[0].description, color=infos["couleur"])
            embed.set_thumbnail(url=spotify_activity.album_cover_url)
            barre = generer_barre_progression(infos["start_time"], spotify_activity.duration)
            embed.add_field(name="Progression", value=barre, inline=False)
            embed.add_field(name="Écouter sur Spotify", value=f"[Clique ici]({spotify_activity.track_url})", inline=False)
            view = LikeView(spotify_activity.title, spotify_activity.artist, spotify_activity.track_url, spotify_activity.album_cover_url)
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
@app_commands.guild_only()
async def voir_historique(interaction: discord.Interaction, page: int = 1, membre: discord.Member = None):
    if page < 1:
        page = 1

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
