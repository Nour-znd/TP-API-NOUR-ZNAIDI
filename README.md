# My Social Networks API

API REST en Python pour gérer les utilisateurs, groupes, événements et leurs fonctions sociales. Le projet utilise uniquement la bibliothèque standard.

## Démarrer

Python 3.10+ :

```bash
python server.py
```

URL de base : `http://127.0.0.1:8000/api/v1`. Importer `postman/My Social Networks API.postman_collection.json` dans Postman. Créer d'abord des utilisateurs, puis un groupe ou un événement et utiliser les identifiants retournés.

## Ressources

Les listes acceptent `GET` avec `?q=terme`; une ressource accepte `GET`, `PATCH` et `DELETE`. `POST` crée une ressource. `PUT` remplace les champs modifiables. Les réponses sont en JSON; les erreurs ont la forme `{"error":"..."}`.

| Ressource | Routes principales |
|---|---|
| Utilisateurs | `/users`, `/users/{id}` |
| Groupes | `/groups`, `/groups/{id}`, `/groups/{id}/members`, `/groups/{id}/admins` |
| Événements | `/events`, `/events/{id}`, `/events/{id}/participants`, `/events/{id}/organizers` |
| Discussion | `/groups/{id}/thread` ou `/events/{id}/thread`, puis `/threads/{id}/messages` et `/messages/{id}/comments` |
| Photos | `/events/{id}/albums`, `/albums/{id}/photos`, `/photos/{id}/comments` |
| Sondages | `/events/{id}/polls`, `/polls/{id}/responses` |
| Billetterie | `/events/{id}/ticket-types`, `/events/{id}/tickets` |
| Shopping list | `/events/{id}/shopping-list` |
| Covoiturage | `/events/{id}/carpools` |

Les sous-ressources suivent les mêmes opérations CRUD (création, lecture, modification et suppression). `/health` indique si le service répond.

## Principales règles

- Email utilisateur unique, sans distinction de casse.
- Un groupe a au moins un administrateur; visibilité `public`, `private` ou `secret`.
- Un événement a au moins un organisateur, des participants, un lieu, une date de début et de fin, et une visibilité `public` ou `private`. Les dates sont en ISO 8601, par exemple `2026-12-05T18:00:00-01:00`.
- Un fil est rattaché à un seul groupe ou événement. Les fils de groupe respectent l'option autorisant les membres à publier.
- Les photos sont référencées par URL. L'ajout et les commentaires sont réservés aux participants de l'événement.
- Un sondage contient une ou plusieurs questions, avec au moins deux choix par question. Un participant choisit un choix pour chaque question.
- Les billets sont limités par le stock du type; une personne ne peut acheter qu'un billet par événement. L'annulation remet le billet en stock.
- Le nom d'un article de shopping list est unique par événement. Les trajets stockent l'écart maximal en minutes.
- La billetterie, la shopping list et le covoiturage doivent être activés dans l'événement.
