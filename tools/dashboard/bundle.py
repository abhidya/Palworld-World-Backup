"""Bundle dashboard.html + data.js into a single shareable file."""
import io, os
HERE = os.path.dirname(os.path.abspath(__file__))
html = io.open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
data = io.open(os.path.join(HERE, "data.js"), encoding="utf-8").read()
out = html.replace('<script src="data.js"></script>', "<script>" + data + "</script>")
tr_path = os.path.join(HERE, "trends.js")
trends = io.open(tr_path, encoding="utf-8").read() if os.path.exists(tr_path) else "window.PALTRENDS=[];"
out = out.replace('<script src="trends.js" onerror="window.PALTRENDS=[]"></script>', "<script>" + trends + "</script>")
io.open(os.path.join(HERE, "palworld-dashboard.html"), "w", encoding="utf-8").write(out)
print("wrote palworld-dashboard.html", round(os.path.getsize(os.path.join(HERE, "palworld-dashboard.html")) / 1e6, 2), "MB")
