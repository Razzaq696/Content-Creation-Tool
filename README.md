# Abdul Tool — Flask API Server

## Deploy on Railway.app (FREE)

### Step 1 — GitHub pe upload karo
```
git init
git add .
git commit -m "Abdul Tool API"
git remote add origin https://github.com/YOUR_USERNAME/abdultool-api.git
git push -u origin main
```

### Step 2 — Railway.app
1. railway.app pe jao — GitHub se login karo
2. "New Project" → "Deploy from GitHub repo"
3. Apna repo select karo
4. Environment Variables set karo:
   - API_KEY = abdultool-secret-2024

### Step 3 — URL copy karo
Deploy hone ke baad Railway ek URL dega:
https://abdultool-api-production.up.railway.app

### Step 4 — Android app mein URL daalo
ApiClient.java mein:
```java
public static String BASE_URL = "https://abdultool-api-production.up.railway.app";
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | /        | Health check |
| POST   | /download | Start video download |
| GET    | /job/{id} | Check job status |
| POST   | /split   | Split video into clips |
| POST   | /face-filter | Run face detection |
| GET    | /clips/{id} | List clips |

## Headers (required)
X-API-Key: abdultool-secret-2024
Content-Type: application/json
