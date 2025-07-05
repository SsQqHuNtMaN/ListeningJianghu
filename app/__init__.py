from .reader import transcripts_bp
from flask import Flask

__all__=['app']

app=Flask(__name__)
app.register_blueprint(transcripts_bp)
