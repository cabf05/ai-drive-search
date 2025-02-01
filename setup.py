# setup.py
import os
import json
import requests
from flask import Flask, request, redirect, session, render_template_string
from flask_session import Session
from msal import PublicClientApplication
from openai import OpenAI
from io import BytesIO
from PyPDF2 import PdfReader
from docx import Document

app = Flask(__name__name__)
app.secret_key = os.urandom(24)
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configuração inicial (persistente no serverless)
CONFIG_FILE = "config.json"

# Template HTML
HTML_TEMPLATE = '''
<!doctype html>
<html>
<head>
    <title>AI Drive Search</title>
    <style>
        body { max-width: 800px; margin: 20px auto; padding: 20px; }
        .section { margin: 20px 0; padding: 20px; border: 1px solid #ddd; }
    </style>
</head>
<body>
    <h1>Smart Document Manager</h1>
    
    {% if not session.configured %}
    <div class="section">
        <h2>Configuração Inicial</h2>
        <form method="post" action="/configure">
            <h3>OpenAI</h3>
            <input type="password" name="openai_key" placeholder="Chave API OpenAI" required>
            
            <h3>OneDrive</h3>
            <button type="button" onclick="location.href='/connect'">Conectar OneDrive</button>
            
            <button type="submit">Salvar Configuração</button>
        </form>
    </div>
    
    {% else %}
    <div class="section">
        <h2>Busca Inteligente</h2>
        <form action="/search" method="get">
            <input type="text" name="query" placeholder="Pesquisar documentos..." style="width: 300px;">
            <button type="submit">Buscar</button>
        </form>
        
        {% if results %}
        <h3>Resultados:</h3>
        <ul>
            {% for result in results %}
            <li>
                {{ result.name }}<br>
                <small>Confiança: {{ "%.0f"|format(result.score*100) }}%</small>
            </li>
            {% endfor %}
        </ul>
        {% endif %}
    </div>
    {% endif %}
</body>
</html>
'''

# Helpers
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {"openai_key": "", "onedrive_token": ""}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

def get_onedrive_client():
    return PublicClientApplication(
        client_id="d17e5e3d-cc13-4059-8eb7-ff1d4b4a8c6b",  # Microsoft Test App
        authority="https://login.microsoftonline.com/common"
    )

def process_file(content, filename):
    try:
        if filename.endswith('.pdf'):
            reader = PdfReader(BytesIO(content))
            return " ".join([page.extract_text() for page in reader.pages])
        elif filename.endswith('.docx'):
            doc = Document(BytesIO(content))
            return " ".join([para.text for para in doc.paragraphs])
        return ""
    except Exception as e:
        print(f"Error processing {filename}: {str(e)}")
        return ""

# Rotas
@app.route("/")
def home():
    config = load_config()
    session["configured"] = bool(config.get("openai_key") and config.get("onedrive_token"))
    return render_template_string(HTML_TEMPLATE, results=request.args.get("results"))

@app.route("/configure", methods=["POST"])
def configure():
    config = load_config()
    config["openai_key"] = request.form.get("openai_key")
    save_config(config)
    return redirect("/")

@app.route("/connect")
def connect():
    msal = get_onedrive_client()
    auth_url = msal.get_authorization_request_url(
        scopes=["Files.Read.All"],
        redirect_uri=request.url_root + "callback"
    )
    return redirect(auth_url)

@app.route("/callback")
def callback():
    msal = get_onedrive_client()
    result = msal.acquire_token_by_authorization_code(
        code=request.args["code"],
        scopes=["Files.Read.All"],
        redirect_uri=request.url_root + "callback"
    )
    
    config = load_config()
    config["onedrive_token"] = result.get("access_token")
    save_config(config)
    
    return redirect("/")

@app.route("/search")
def search():
    config = load_config()
    
    # Busca no OneDrive
    headers = {"Authorization": f"Bearer {config['onedrive_token']}"}
    files = requests.get(
        "https://graph.microsoft.com/v1.0/me/drive/root/search(q='')",
        headers=headers
    ).json().get("value", [])
    
    # Processar arquivos
    documents = []
    for file in files[:50]:  # Limite para demo
        if file["name"].split(".")[-1] in ["pdf", "docx"]:
            content = requests.get(file["@microsoft.graph.downloadUrl"]).content
            text = process_file(content, file["name"])
            documents.append({"name": file["name"], "content": text})
    
    # Busca com OpenAI
    client = OpenAI(api_key=config["openai_key"])
    query = request.args.get("query")
    
    embeddings = client.embeddings.create(
        input=[doc["content"] for doc in documents] + [query],
        model="text-embedding-3-small"
    )
    
    # Calcular similaridade
    query_embedding = embeddings.data[-1].embedding
    results = []
    for idx, doc in enumerate(documents):
        score = cosine_similarity(
            [query_embedding],
            [embeddings.data[idx].embedding]
        )[0][0]
        results.append({"name": doc["name"], "score": score})
    
    return render_template_string(HTML_TEMPLATE, 
                                results=sorted(results, key=lambda x: x["score"], reverse=True)[:5])

if __name__ == "__main__":
    app.run()
