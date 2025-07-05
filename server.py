from dotenv import load_dotenv
from app import app

load_dotenv()

if __name__=="__main__":
    print(app.url_map)
    app.run(debug=True)
