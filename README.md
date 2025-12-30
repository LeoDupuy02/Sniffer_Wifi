Distance de Mahalanobis

@app.get("/podo")
async def read_podo():
    file_path = os.path.join(os.path.dirname(__file__), "podo.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"message": "Podo asked"}