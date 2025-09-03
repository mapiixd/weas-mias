# weas-mias

Repositorio de scripts útiles para automatizar tareas relacionadas con la descarga de imágenes desde páginas web.

## Proyectos incluidos

### 1. download_images.py

**Descripción:**  
Descarga todas las imágenes encontradas en una página web específica.

**Uso:**
```bash
python download_images.py <URL> -o <carpeta_salida> -t <hilos>
```
**Ejemplo:**
```bash
python download_images.py "https://blog.myl.cl/relatos-hijos-de-daana" -o ../recursos-myl/daana-aniv -t 12
```

**Dependencias:**
- requests
- beautifulsoup4

**Instalación de dependencias:**
```bash
pip install requests beautifulsoup4
```

---

### 2. download_images_dynamic.py

**Descripción:**  
Renderiza páginas con scroll infinito y descarga todas las imágenes visibles/cargadas por JavaScript.

**Uso:**
```bash
python download_images_dynamic.py <URL> -o <carpeta_salida> -t <hilos>
```
**Ejemplo:**
```bash
python download_images_dynamic.py "https://blog.myl.cl/relatos-hijos-de-daana" -o ../../recursos-myl/daana-aniv -t 12
```

**Dependencias:**
- playwright
- beautifulsoup4
- requests

**Instalación de dependencias:**
```bash
pip install playwright beautifulsoup4 requests
python -m playwright install chromium
```

---

## Recomendaciones

- Usa un entorno virtual (`venv`) para instalar las dependencias.
- Guarda las dependencias instaladas con:
	```bash
	pip freeze > requirements.txt
	```
