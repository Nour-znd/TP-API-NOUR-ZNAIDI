import json
import re
import threading
import uuid
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


db = {name: {} for name in (
    "users", "groups", "events", "threads", "messages", "message_comments",
    "albums", "photos", "photo_comments", "polls", "poll_responses",
    "ticket_types", "tickets", "shopping_items", "carpools",
)}
lock = threading.RLock()


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def new_id():
    return str(uuid.uuid4())


def require(data, key, kind=str):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise ApiError(400, f"'{key}' est obligatoire.")
    return value


def check_date(value, field):
    try:
        if not isinstance(value, str) or ("T" not in value and " " not in value): raise ValueError()
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise ApiError(400, f"'{field}' doit être une date et heure ISO 8601.")


def check_time(value, field):
    try:
        time.fromisoformat(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{field}' doit être une heure au format HH:MM.")


def find(table, object_id, label="Ressource"):
    value = db[table].get(object_id)
    if value is None:
        raise ApiError(404, f"{label} introuvable.")
    return value


def clean(value):
    return " ".join(value.casefold().split())


def as_list(value, field, minimum=0):
    if not isinstance(value, list) or len(value) < minimum or any(not isinstance(x, str) for x in value):
        raise ApiError(400, f"'{field}' doit être une liste valide.")
    if len(set(value)) != len(value):
        raise ApiError(400, f"'{field}' ne peut pas contenir de doublons.")
    return value


def check_users(ids):
    for user_id in ids:
        find("users", user_id, "Utilisateur")


def check_visibility(value, allowed):
    if value not in allowed:
        raise ApiError(400, "Valeur de visibilité invalide.")


def is_member(group, user_id):
    return user_id in group["member_ids"]


def is_event_user(event, user_id):
    return user_id in event["participant_ids"] or user_id in event["organizer_ids"]


def can_post(thread, user_id):
    if thread["target_type"] == "event":
        event = find("events", thread["target_id"], "Événement")
        return is_event_user(event, user_id)
    group = find("groups", thread["target_id"], "Groupe")
    return is_member(group, user_id) and (group["members_can_post"] or user_id in group["admin_ids"])


def check_event_module(event_id, module):
    event = find("events", event_id, "Événement")
    if not event[module]:
        raise ApiError(409, "Cette fonctionnalité n'est pas activée pour l'événement.")
    return event


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "MySocialNetworksAPI/2.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ApiError(413, "Corps de requête trop volumineux.")
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "JSON invalide.")
        if not isinstance(value, dict):
            raise ApiError(400, "Le corps doit être un objet JSON.")
        return value

    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self): self.handle("GET")
    def do_POST(self): self.handle("POST")
    def do_PUT(self): self.handle("PUT")
    def do_PATCH(self): self.handle("PATCH")
    def do_DELETE(self): self.handle("DELETE")

    def handle(self, method):
        try:
            with lock:
                parsed = urlparse(self.path)
                data = self.body() if method in ("POST", "PUT", "PATCH") else {}
                result, status = self.route(method, parsed, data)
            self.send_json(status, result)
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})
        except Exception as error:
            self.send_json(500, {"error": "Erreur interne.", "detail": str(error)})

    def route(self, method, parsed, data):
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/api/v1/health" and method == "GET":
            return {"status": "ok", "version": "2.0.0"}, 200

        for name in ("users", "groups", "events"):
            if path == f"/api/v1/{name}":
                return self.root_resource(method, name, data, query)
        m = re.fullmatch(r"/api/v1/(users|groups|events)/([^/]+)", path)
        if m:
            return self.single_resource(method, *m.groups(), data, query)

        m = re.fullmatch(r"/api/v1/groups/([^/]+)/(members|admins)(?:/([^/]+))?", path)
        if m:
            return self.group_people(method, *m.groups(), data)
        m = re.fullmatch(r"/api/v1/events/([^/]+)/(participants|organizers)(?:/([^/]+))?", path)
        if m:
            return self.event_people(method, *m.groups(), data)

        m = re.fullmatch(r"/api/v1/(groups|events)/([^/]+)/thread", path)
        if m:
            return self.thread(method, *m.groups())
        m = re.fullmatch(r"/api/v1/threads/([^/]+)/messages(?:/([^/]+))?", path)
        if m:
            return self.messages(method, *m.groups(), data, query)
        m = re.fullmatch(r"/api/v1/messages/([^/]+)/comments(?:/([^/]+))?", path)
        if m:
            return self.comments(method, "message", *m.groups(), data, query)

        m = re.fullmatch(r"/api/v1/events/([^/]+)/albums(?:/([^/]+))?", path)
        if m:
            return self.albums(method, *m.groups(), data, query)
        m = re.fullmatch(r"/api/v1/albums/([^/]+)/photos(?:/([^/]+))?", path)
        if m:
            return self.photos(method, *m.groups(), data, query)
        m = re.fullmatch(r"/api/v1/photos/([^/]+)/comments(?:/([^/]+))?", path)
        if m:
            return self.comments(method, "photo", *m.groups(), data, query)

        m = re.fullmatch(r"/api/v1/events/([^/]+)/polls(?:/([^/]+))?", path)
        if m:
            return self.polls(method, *m.groups(), data, query)
        m = re.fullmatch(r"/api/v1/polls/([^/]+)/responses(?:/([^/]+))?", path)
        if m:
            return self.poll_responses(method, *m.groups(), data, query)

        m = re.fullmatch(r"/api/v1/events/([^/]+)/(ticket-types|tickets|shopping-list|carpools)(?:/([^/]+))?", path)
        if m:
            return self.event_feature(method, *m.groups(), data, query)
        raise ApiError(404, "Endpoint introuvable.")

    def root_resource(self, method, name, data, query):
        if method == "GET":
            rows = list(db[name].values())
            term = query.get("q", [""])[0].casefold()
            if term:
                rows = [row for row in rows if term in json.dumps(row, ensure_ascii=False).casefold()]
            if name == "groups":
                viewer = query.get("user_id", [""])[0]
                rows = [g for g in rows if g["visibility"] == "public" or is_member(g, viewer)]
            if name == "events":
                viewer = query.get("user_id", [""])[0]
                rows = [e for e in rows if e["visibility"] == "public" or is_event_user(e, viewer)]
            return rows, 200
        if method == "POST":
            if name == "users": return self.create_user(data), 201
            if name == "groups": return self.create_group(data), 201
            return self.create_event(data), 201
        raise ApiError(405, "Méthode non autorisée.")

    def single_resource(self, method, name, object_id, data, query):
        row = find(name, object_id, name[:-1].capitalize())
        if name == "groups" and row["visibility"] != "public" and not is_member(row, query.get("user_id", [""])[0]):
            raise ApiError(404, "Groupe introuvable.")
        if name == "events" and row["visibility"] != "public" and not is_event_user(row, query.get("user_id", [""])[0]):
            raise ApiError(404, "Événement introuvable.")
        if method == "GET": return row, 200
        if method in ("PUT", "PATCH"):
            if name == "users": return self.update_user(row, data, method == "PUT"), 200
            if name == "groups": return self.update_group(row, data, method == "PUT"), 200
            return self.update_event(row, data, method == "PUT"), 200
        if method == "DELETE":
            del db[name][object_id]
            return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def create_user(self, data):
        email = require(data, "email").strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ApiError(400, "Adresse email invalide.")
        if any(user["email"] == email for user in db["users"].values()):
            raise ApiError(409, "Cette adresse email est déjà utilisée.")
        row = {"id": new_id(), "first_name": require(data, "first_name"), "last_name": require(data, "last_name"),
               "email": email, "bio": data.get("bio", ""), "profile_photo": data.get("profile_photo", ""),
               "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        db["users"][row["id"]] = row
        return row

    def update_user(self, row, data, replace):
        merged = data if replace else {**row, **data}
        email = require(merged, "email").strip().casefold()
        if any(user["id"] != row["id"] and user["email"] == email for user in db["users"].values()):
            raise ApiError(409, "Cette adresse email est déjà utilisée.")
        row.update(first_name=require(merged, "first_name"), last_name=require(merged, "last_name"), email=email,
                   bio=merged.get("bio", ""), profile_photo=merged.get("profile_photo", ""))
        return row

    def create_group(self, data):
        admin_ids = as_list(data.get("admin_ids", [data.get("creator_id")]), "admin_ids", 1)
        check_users(admin_ids)
        visibility = data.get("visibility", "private")
        check_visibility(visibility, ("public", "private", "secret"))
        row = {"id": new_id(), "name": require(data, "name"), "description": data.get("description", ""),
               "icon": data.get("icon", ""), "cover_photo": data.get("cover_photo", ""), "visibility": visibility,
               "members_can_post": bool(data.get("members_can_post", True)),
               "members_can_create_events": bool(data.get("members_can_create_events", False)),
               "admin_ids": admin_ids, "member_ids": list(admin_ids)}
        db["groups"][row["id"]] = row
        return row

    def update_group(self, row, data, replace):
        merged = data if replace else {**row, **data}
        visibility = merged.get("visibility", "private")
        check_visibility(visibility, ("public", "private", "secret"))
        admins = as_list(merged.get("admin_ids", row["admin_ids"]), "admin_ids", 1)
        check_users(admins)
        members = list(dict.fromkeys(row["member_ids"] + admins))
        row.update(name=require(merged, "name"), description=merged.get("description", ""), icon=merged.get("icon", ""),
                   cover_photo=merged.get("cover_photo", ""), visibility=visibility,
                   members_can_post=bool(merged.get("members_can_post", True)),
                   members_can_create_events=bool(merged.get("members_can_create_events", False)),
                   admin_ids=admins, member_ids=members)
        return row

    def group_people(self, method, group_id, collection, user_id, data):
        group = find("groups", group_id, "Groupe")
        key = "member_ids" if collection == "members" else "admin_ids"
        if user_id is None:
            if method == "GET": return [{"id": uid, "user": db["users"].get(uid)} for uid in group[key]], 200
            if method == "POST":
                uid = require(data, "user_id")
                find("users", uid, "Utilisateur")
                actor = data.get("actor_id", uid if key == "member_ids" else None)
                if actor != uid and actor not in group["admin_ids"]: raise ApiError(403, "Action réservée aux administrateurs.")
                if key == "admin_ids" and actor not in group["admin_ids"]: raise ApiError(403, "Action réservée aux administrateurs.")
                if uid not in group[key]: group[key].append(uid)
                if key == "admin_ids" and uid not in group["member_ids"]: group["member_ids"].append(uid)
                return group, 200
        else:
            if method == "DELETE":
                if user_id not in group[key]: raise ApiError(404, "Utilisateur absent du groupe.")
                actor = data.get("actor_id", user_id)
                if actor != user_id and actor not in group["admin_ids"]: raise ApiError(403, "Action réservée aux administrateurs.")
                if key == "admin_ids" and actor not in group["admin_ids"]: raise ApiError(403, "Action réservée aux administrateurs.")
                if key == "admin_ids" and len(group[key]) == 1: raise ApiError(409, "Un groupe doit garder au moins un administrateur.")
                if key == "member_ids" and user_id in group["admin_ids"]: raise ApiError(409, "Retirez d'abord son rôle d'administrateur.")
                group[key].remove(user_id)
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def create_event(self, data):
        creator = data.get("creator_id")
        organizers = as_list(data.get("organizer_ids", [creator] if creator else []), "organizer_ids", 1)
        if creator and creator not in organizers: organizers.append(creator)
        check_users(organizers)
        participants = as_list(data.get("participant_ids", []), "participant_ids")
        check_users(participants)
        participants = list(dict.fromkeys(organizers + participants))
        start, end = require(data, "start_at"), require(data, "end_at")
        check_date(start, "start_at")
        check_date(end, "end_at")
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone()
        if end_dt < start_dt:
            raise ApiError(400, "La date de fin doit suivre la date de début.")
        visibility = data.get("visibility", "public")
        check_visibility(visibility, ("public", "private"))
        group_id = data.get("group_id")
        if group_id:
            group = find("groups", group_id, "Groupe")
            if creator not in group["admin_ids"] and not group["members_can_create_events"]:
                raise ApiError(403, "Les membres du groupe ne sont pas autorisés à créer des événements.")
            if data.get("invite_group_members"):
                participants.extend(group["member_ids"])
                participants = list(dict.fromkeys(participants))
        row = {"id": new_id(), "name": require(data, "name"), "description": data.get("description", ""),
               "start_at": start, "end_at": end, "location": require(data, "location"), "cover_photo": data.get("cover_photo", ""),
               "visibility": visibility, "group_id": group_id, "organizer_ids": organizers, "participant_ids": participants,
               "tickets_enabled": bool(data.get("tickets_enabled", False)),
               "shopping_list_enabled": bool(data.get("shopping_list_enabled", False)),
               "carpool_enabled": bool(data.get("carpool_enabled", False))}
        db["events"][row["id"]] = row
        return row

    def update_event(self, row, data, replace):
        merged = data if replace else {**row, **data}
        organizers = as_list(merged.get("organizer_ids", row["organizer_ids"]), "organizer_ids", 1)
        participants = as_list(merged.get("participant_ids", row["participant_ids"]), "participant_ids")
        check_users(organizers + participants)
        participants = list(dict.fromkeys(organizers + participants))
        start, end = require(merged, "start_at"), require(merged, "end_at")
        check_date(start, "start_at")
        check_date(end, "end_at")
        check_visibility(merged.get("visibility", "public"), ("public", "private"))
        row.update(name=require(merged, "name"), description=merged.get("description", ""), start_at=start, end_at=end,
                   location=require(merged, "location"), cover_photo=merged.get("cover_photo", ""),
                   visibility=merged.get("visibility", "public"), organizer_ids=organizers, participant_ids=participants,
                   tickets_enabled=bool(merged.get("tickets_enabled", False)),
                   shopping_list_enabled=bool(merged.get("shopping_list_enabled", False)),
                   carpool_enabled=bool(merged.get("carpool_enabled", False)))
        return row

    def event_people(self, method, event_id, collection, user_id, data):
        event = find("events", event_id, "Événement")
        key = "participant_ids" if collection == "participants" else "organizer_ids"
        if user_id is None:
            if method == "GET": return [{"id": uid, "user": db["users"].get(uid)} for uid in event[key]], 200
            if method == "POST":
                uid = require(data, "user_id")
                find("users", uid, "Utilisateur")
                actor = data.get("actor_id", uid if key == "participant_ids" else None)
                if actor != uid and actor not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
                if key == "organizer_ids" and actor not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
                if uid not in event[key]: event[key].append(uid)
                if key == "organizer_ids" and uid not in event["participant_ids"]: event["participant_ids"].append(uid)
                return event, 200
        elif method == "DELETE":
            if user_id not in event[key]: raise ApiError(404, "Utilisateur absent de l'événement.")
            actor = data.get("actor_id", user_id)
            if actor != user_id and actor not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
            if key == "organizer_ids" and actor not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
            if key == "organizer_ids" and len(event[key]) == 1: raise ApiError(409, "Un événement doit garder au moins un organisateur.")
            if key == "participant_ids" and user_id in event["organizer_ids"]: raise ApiError(409, "Retirez d'abord son rôle d'organisateur.")
            event[key].remove(user_id)
            return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def thread(self, method, target, target_id):
        item = find("groups" if target == "groups" else "events", target_id, "Groupe" if target == "groups" else "Événement")
        key = ("group" if target == "groups" else "event") + ":" + target_id
        if method == "GET":
            if key not in db["threads"]: raise ApiError(404, "Fil introuvable.")
            return db["threads"][key], 200
        if method == "POST":
            if key in db["threads"]: raise ApiError(409, "Un fil existe déjà pour cette ressource.")
            row = {"id": new_id(), "target_type": target[:-1], "target_id": target_id}
            db["threads"][key] = row
            return row, 201
        if method == "DELETE":
            if key not in db["threads"]: raise ApiError(404, "Fil introuvable.")
            del db["threads"][key]
            return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def thread_for(self, thread_id):
        for row in db["threads"].values():
            if row["id"] == thread_id: return row
        raise ApiError(404, "Fil introuvable.")

    def messages(self, method, thread_id, message_id, data, query):
        thread = self.thread_for(thread_id)
        rows = [m for m in db["messages"].values() if m["thread_id"] == thread_id]
        if message_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                user_id = require(data, "user_id")
                find("users", user_id, "Utilisateur")
                if not can_post(thread, user_id): raise ApiError(403, "Cet utilisateur ne peut pas publier dans ce fil.")
                row = {"id": new_id(), "thread_id": thread_id, "user_id": user_id, "content": require(data, "content"),
                       "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                db["messages"][row["id"]] = row
                return row, 201
        else:
            row = find("messages", message_id, "Message")
            if row["thread_id"] != thread_id: raise ApiError(404, "Message introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                actor = data.get("user_id", row["user_id"])
                if actor != row["user_id"] and not can_post(thread, actor): raise ApiError(403, "Modification non autorisée.")
                if "content" in data: row["content"] = require(data, "content")
                return row, 200
            if method == "DELETE":
                actor = data.get("user_id", row["user_id"])
                if actor != row["user_id"] and not can_post(thread, actor): raise ApiError(403, "Suppression non autorisée.")
                del db["messages"][message_id]
                for key in [k for k,v in db["message_comments"].items() if v["message_id"] == message_id]: del db["message_comments"][key]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def comments(self, method, target, parent_id, comment_id, data, query):
        table = "message_comments" if target == "message" else "photo_comments"
        parent_table = "messages" if target == "message" else "photos"
        foreign = "message_id" if target == "message" else "photo_id"
        find(parent_table, parent_id, "Message" if target == "message" else "Photo")
        event = None
        thread = None
        if target == "message":
            message = find("messages", parent_id, "Message")
            thread = self.thread_for(message["thread_id"])
            if thread["target_type"] == "event": event = find("events", thread["target_id"], "Événement")
            elif thread["target_type"] == "group":
                event = None
        else:
            photo = find("photos", parent_id, "Photo")
            album = find("albums", photo["album_id"], "Album")
            event = find("events", album["event_id"], "Événement")
        rows = [c for c in db[table].values() if c[foreign] == parent_id]
        if comment_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                user_id = require(data, "user_id")
                find("users", user_id, "Utilisateur")
                if thread and not can_post(thread, user_id): raise ApiError(403, "Cet utilisateur ne peut pas commenter ce fil.")
                if event and not is_event_user(event, user_id): raise ApiError(403, "Seul un participant peut commenter.")
                row = {"id": new_id(), foreign: parent_id, "user_id": user_id, "content": require(data, "content"),
                       "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                db[table][row["id"]] = row
                return row, 201
        else:
            row = find(table, comment_id, "Commentaire")
            if row[foreign] != parent_id: raise ApiError(404, "Commentaire introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                row["content"] = require(data, "content")
                return row, 200
            if method == "DELETE":
                del db[table][comment_id]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def albums(self, method, event_id, album_id, data, query):
        find("events", event_id, "Événement")
        rows = [a for a in db["albums"].values() if a["event_id"] == event_id]
        if album_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                row = {"id": new_id(), "event_id": event_id, "name": require(data, "name"), "description": data.get("description", "")}
                db["albums"][row["id"]] = row
                return row, 201
        else:
            row = find("albums", album_id, "Album")
            if row["event_id"] != event_id: raise ApiError(404, "Album introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                if "name" in data: row["name"] = require(data, "name")
                if "description" in data: row["description"] = data["description"]
                return row, 200
            if method == "DELETE":
                del db["albums"][album_id]
                for key in [k for k,v in db["photos"].items() if v["album_id"] == album_id]: del db["photos"][key]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def photos(self, method, album_id, photo_id, data, query):
        album = find("albums", album_id, "Album")
        event = find("events", album["event_id"], "Événement")
        rows = [p for p in db["photos"].values() if p["album_id"] == album_id]
        if photo_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                user_id = require(data, "user_id")
                if not is_event_user(event, user_id): raise ApiError(403, "Seul un participant peut ajouter une photo.")
                find("users", user_id, "Utilisateur")
                url = require(data, "url")
                row = {"id": new_id(), "album_id": album_id, "user_id": user_id, "url": url, "caption": data.get("caption", ""),
                       "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                db["photos"][row["id"]] = row
                return row, 201
        else:
            row = find("photos", photo_id, "Photo")
            if row["album_id"] != album_id: raise ApiError(404, "Photo introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                if "url" in data: row["url"] = require(data, "url")
                if "caption" in data: row["caption"] = data["caption"]
                return row, 200
            if method == "DELETE":
                del db["photos"][photo_id]
                for key in [k for k,v in db["photo_comments"].items() if v["photo_id"] == photo_id]: del db["photo_comments"][key]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def polls(self, method, event_id, poll_id, data, query):
        event = find("events", event_id, "Événement")
        rows = [p for p in db["polls"].values() if p["event_id"] == event_id]
        if poll_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                organizer = require(data, "organizer_id")
                if organizer not in event["organizer_ids"]: raise ApiError(403, "Seul un organisateur peut créer un sondage.")
                questions = self.make_questions(data.get("questions"))
                row = {"id": new_id(), "event_id": event_id, "organizer_id": organizer, "title": require(data, "title"), "questions": questions}
                db["polls"][row["id"]] = row
                return row, 201
        else:
            row = find("polls", poll_id, "Sondage")
            if row["event_id"] != event_id: raise ApiError(404, "Sondage introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                if "title" in data: row["title"] = require(data, "title")
                if "questions" in data:
                    if any(r["poll_id"] == poll_id for r in db["poll_responses"].values()):
                        raise ApiError(409, "Les questions ne peuvent plus être modifiées après une réponse.")
                    row["questions"] = self.make_questions(data["questions"])
                return row, 200
            if method == "DELETE":
                del db["polls"][poll_id]
                for key in [k for k,v in db["poll_responses"].items() if v["poll_id"] == poll_id]: del db["poll_responses"][key]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def make_questions(self, value):
        if not isinstance(value, list) or not value: raise ApiError(400, "Un sondage doit contenir au moins une question.")
        result = []
        for item in value:
            if not isinstance(item, dict): raise ApiError(400, "Question invalide.")
            options = item.get("options")
            if not isinstance(options, list) or len(options) < 2: raise ApiError(400, "Chaque question doit proposer au moins deux réponses.")
            result.append({"id": new_id(), "text": require(item, "text"),
                           "options": [{"id": new_id(), "text": require({"text": x}, "text")} for x in options]})
        return result

    def poll_responses(self, method, poll_id, response_id, data, query):
        poll = find("polls", poll_id, "Sondage")
        event = find("events", poll["event_id"], "Événement")
        rows = [r for r in db["poll_responses"].values() if r["poll_id"] == poll_id]
        if response_id is None:
            if method == "GET": return rows, 200
            if method == "POST":
                user_id = require(data, "user_id")
                if not is_event_user(event, user_id): raise ApiError(403, "Seul un participant peut répondre.")
                if any(r["user_id"] == user_id for r in rows): raise ApiError(409, "Une réponse existe déjà pour ce participant.")
                row = {"id": new_id(), "poll_id": poll_id, "user_id": user_id, "answers": self.validate_answers(poll, data.get("answers"))}
                db["poll_responses"][row["id"]] = row
                return row, 201
        else:
            row = find("poll_responses", response_id, "Réponse")
            if row["poll_id"] != poll_id: raise ApiError(404, "Réponse introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"):
                row["answers"] = self.validate_answers(poll, data.get("answers"))
                return row, 200
            if method == "DELETE":
                del db["poll_responses"][response_id]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def validate_answers(self, poll, answers):
        if not isinstance(answers, dict): raise ApiError(400, "'answers' doit associer chaque question à une réponse.")
        expected = {q["id"] for q in poll["questions"]}
        if set(answers) != expected: raise ApiError(400, "Une réponse doit être fournie pour chaque question et seulement celles-ci.")
        for question in poll["questions"]:
            if answers[question["id"]] not in {o["id"] for o in question["options"]}:
                raise ApiError(400, "La réponse choisie n'appartient pas à la question.")
        return answers

    def event_feature(self, method, event_id, resource, object_id, data, query):
        module = {"ticket-types": "tickets_enabled", "tickets": "tickets_enabled", "shopping-list": "shopping_list_enabled", "carpools": "carpool_enabled"}[resource]
        event = check_event_module(event_id, module)
        table = {"ticket-types": "ticket_types", "tickets": "tickets", "shopping-list": "shopping_items", "carpools": "carpools"}[resource]
        if object_id is None:
            rows = [v for v in db[table].values() if v["event_id"] == event_id]
            if method == "GET":
                term = query.get("q", [""])[0].casefold()
                return [v for v in rows if term in json.dumps(v, ensure_ascii=False).casefold()] if term else rows, 200
            if method == "POST": return self.create_feature(resource, event, data), 201
        else:
            row = find(table, object_id)
            if row["event_id"] != event_id: raise ApiError(404, "Ressource introuvable.")
            if method == "GET": return row, 200
            if method in ("PUT", "PATCH"): return self.update_feature(resource, event, row, data, method == "PUT"), 200
            if method == "DELETE":
                if resource == "ticket-types" and row["sold"]: raise ApiError(409, "Ce type a déjà des billets vendus.")
                if resource == "tickets": db["ticket_types"][row["ticket_type_id"]]["sold"] -= 1
                del db[table][object_id]
                return {"deleted": True}, 200
        raise ApiError(405, "Méthode non autorisée.")

    def create_feature(self, resource, event, data):
        event_id = event["id"]
        if resource == "ticket-types":
            organizer = require(data, "organizer_id")
            if organizer not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
            amount, quantity = data.get("amount"), data.get("quantity")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0: raise ApiError(400, "Montant invalide.")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1: raise ApiError(400, "Quantité invalide.")
            row = {"id": new_id(), "event_id": event_id, "name": require(data, "name"), "amount": amount, "quantity": quantity, "sold": 0}
        elif resource == "tickets":
            type_id = require(data, "ticket_type_id")
            ticket_type = find("ticket_types", type_id, "Type de billet")
            if ticket_type["event_id"] != event_id: raise ApiError(400, "Type de billet associé à un autre événement.")
            person = data.get("person")
            if not isinstance(person, dict): raise ApiError(400, "'person' est obligatoire.")
            for key in ("first_name", "last_name", "address"): require(person, key)
            person = {k: person[k] for k in ("first_name", "last_name", "address")}
            if any(t["event_id"] == event_id and clean(t["person"]["first_name"] + t["person"]["last_name"] + t["person"]["address"]) == clean(person["first_name"] + person["last_name"] + person["address"]) for t in db["tickets"].values()):
                raise ApiError(409, "Cette personne a déjà un billet pour cet événement.")
            if ticket_type["sold"] >= ticket_type["quantity"]: raise ApiError(409, "Stock épuisé.")
            ticket_type["sold"] += 1
            row = {"id": new_id(), "event_id": event_id, "ticket_type_id": type_id, "person": person, "purchase_date": date.today().isoformat()}
        elif resource == "shopping-list":
            user_id = require(data, "user_id")
            if not is_event_user(event, user_id): raise ApiError(403, "Seul un participant peut ajouter un article.")
            name, quantity, arrival = require(data, "name"), data.get("quantity"), require(data, "arrival_time")
            check_time(arrival, "arrival_time")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1: raise ApiError(400, "Quantité invalide.")
            if any(v["event_id"] == event_id and clean(v["name"]) == clean(name) for v in db["shopping_items"].values()): raise ApiError(409, "Cet article est déjà proposé pour cet événement.")
            row = {"id": new_id(), "event_id": event_id, "user_id": user_id, "name": name, "quantity": quantity, "arrival_time": arrival}
        else:
            user_id = require(data, "user_id")
            if not is_event_user(event, user_id): raise ApiError(403, "Seul un participant peut proposer un trajet.")
            departure, departure_time = require(data, "departure_place"), require(data, "departure_time")
            price, seats, detour = data.get("proposed_price"), data.get("seats_available"), data.get("max_detour_minutes")
            check_time(departure_time, "departure_time")
            if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0: raise ApiError(400, "Prix invalide.")
            if not isinstance(seats, int) or isinstance(seats, bool) or seats < 1: raise ApiError(400, "Nombre de places invalide.")
            if not isinstance(detour, int) or isinstance(detour, bool) or detour < 0: raise ApiError(400, "Écart maximal invalide.")
            row = {"id": new_id(), "event_id": event_id, "user_id": user_id, "departure_place": departure, "departure_time": departure_time,
                   "proposed_price": price, "seats_available": seats, "max_detour_minutes": detour}
        db[{"ticket-types":"ticket_types", "tickets":"tickets", "shopping-list":"shopping_items", "carpools":"carpools"}[resource]][row["id"]] = row
        return row

    def update_feature(self, resource, event, row, data, replace):
        merged = data if replace else {**row, **data}
        if resource == "ticket-types":
            organizer = data.get("organizer_id")
            if organizer and organizer not in event["organizer_ids"]: raise ApiError(403, "Action réservée aux organisateurs.")
            amount, quantity = merged.get("amount"), merged.get("quantity")
            if not isinstance(amount, (int,float)) or isinstance(amount,bool) or amount < 0: raise ApiError(400, "Montant invalide.")
            if not isinstance(quantity, int) or isinstance(quantity,bool) or quantity < row["sold"]: raise ApiError(400, "Quantité inférieure au nombre vendu.")
            row.update(name=require(merged,"name"), amount=amount, quantity=quantity)
        elif resource == "tickets":
            type_id = require(merged,"ticket_type_id")
            ticket_type = find("ticket_types", type_id, "Type de billet")
            if ticket_type["event_id"] != event["id"]: raise ApiError(400, "Type de billet invalide.")
            person = merged.get("person")
            if not isinstance(person,dict): raise ApiError(400,"'person' est obligatoire.")
            for key in ("first_name","last_name","address"): require(person,key)
            person = {k:person[k] for k in ("first_name","last_name","address")}
            if any(t["id"] != row["id"] and t["event_id"] == event["id"] and clean(t["person"]["first_name"] + t["person"]["last_name"] + t["person"]["address"]) == clean(person["first_name"] + person["last_name"] + person["address"]) for t in db["tickets"].values()): raise ApiError(409,"Cette personne a déjà un billet.")
            old = db["ticket_types"][row["ticket_type_id"]]
            if old["id"] != ticket_type["id"]:
                if ticket_type["sold"] >= ticket_type["quantity"]: raise ApiError(409,"Stock épuisé.")
                old["sold"] -= 1
                ticket_type["sold"] += 1
            row.update(ticket_type_id=type_id,person=person)
        elif resource == "shopping-list":
            name, quantity, arrival = require(merged,"name"), merged.get("quantity"), require(merged,"arrival_time")
            check_time(arrival,"arrival_time")
            if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<1: raise ApiError(400,"Quantité invalide.")
            if any(v["id"]!=row["id"] and v["event_id"]==event["id"] and clean(v["name"])==clean(name) for v in db["shopping_items"].values()): raise ApiError(409,"Cet article est déjà proposé.")
            row.update(name=name,quantity=quantity,arrival_time=arrival)
        else:
            departure, departure_time = require(merged,"departure_place"), require(merged,"departure_time")
            price,seats,detour=merged.get("proposed_price"),merged.get("seats_available"),merged.get("max_detour_minutes")
            check_time(departure_time,"departure_time")
            if not isinstance(price,(int,float)) or isinstance(price,bool) or price<0: raise ApiError(400,"Prix invalide.")
            if not isinstance(seats,int) or isinstance(seats,bool) or seats<1: raise ApiError(400,"Nombre de places invalide.")
            if not isinstance(detour,int) or isinstance(detour,bool) or detour<0: raise ApiError(400,"Écart maximal invalide.")
            row.update(departure_place=departure,departure_time=departure_time,proposed_price=price,seats_available=seats,max_detour_minutes=detour)
        return row


if __name__ == "__main__":
    address, port = "127.0.0.1", 8000
    print(f"API disponible sur http://{address}:{port}/api/v1")
    ThreadingHTTPServer((address, port), ApiHandler).serve_forever()
