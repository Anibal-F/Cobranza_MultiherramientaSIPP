import os
import sys
from dotenv import load_dotenv
from O365 import Account, FileSystemTokenBackend

# Definir la raíz del proyecto para leer el .env y guardar el token
# (este script ya vive en la raíz, por eso no se sube ningún nivel con '..')
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(ROOT_DIR, '.env')

if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

CLIENT_ID = os.getenv('GRAPH_CLIENT_ID')
TENANT_ID = os.getenv('GRAPH_TENANT_ID')
EMAIL_CUENTA = os.getenv('DESTINATARIO_EMAIL')

def my_consent(consent_url):
    print("\n" + "="*60)
    print("1. Abre este link en tu navegador de internet:")
    print(consent_url)
    print("\n2. Inicia sesión con la cuenta de Microsoft correspondiente.")
    print("3. Al finalizar, la página quedará en blanco. Copia TODA la URL del navegador.")
    print("4. Crea un archivo de texto llamado 'url.txt' en la raíz del proyecto,")
    print("   pega ahí la URL que copiaste y guárdalo.")
    print("="*60)
    
    input("\nUna vez guardado el archivo 'url.txt', presiona ENTER aquí para continuar...")
    
    url_file_path = os.path.join(ROOT_DIR, "url.txt")
    try:
        with open(url_file_path, "r", encoding="utf-8") as f:
            redirect_url = f.read().strip()
        
        # Eliminar el archivo temporal url.txt por limpieza
        if os.path.exists(url_file_path):
            os.remove(url_file_path)
            
        return redirect_url
    except Exception:
        print("❌ No se pudo leer 'url.txt' en la raíz del proyecto.")
        return ""

def main():
    if not CLIENT_ID or not TENANT_ID:
        print("❌ Faltan credenciales de Microsoft Graph en el archivo .env (GRAPH_CLIENT_ID o GRAPH_TENANT_ID)")
        print(f"Buscando archivo .env en: {dotenv_path}")
        return

    print("🔐 Iniciando proceso de autenticación para Office 365...")
    print(f"Cuenta objetivo: {EMAIL_CUENTA or 'No especificada en .env'}")

    credentials = (CLIENT_ID, "")
    
    # Guardar el token en la raíz del proyecto, que es donde lo busca el extractor
    token_backend = FileSystemTokenBackend(token_path=ROOT_DIR, token_filename='o365_token.txt')
    
    account = Account(credentials, auth_flow='authorization', tenant_id=TENANT_ID, token_backend=token_backend)
    
    if account.authenticate(scopes=['basic', 'message_all'], handle_consent=my_consent):
        token_path = os.path.join(ROOT_DIR, 'o365_token.txt')
        print(f"\n✅ ¡Autenticación exitosa!")
        print(f"El token permanente ha sido guardado en: {token_path}")
    else:
        print("\n❌ La autenticación falló. Revisa las credenciales e inténtalo de nuevo.")

if __name__ == "__main__":
    main()
