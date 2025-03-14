# 使用官方 Python 映像檔作為基底 (可依需求選擇版本)
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 複製需求檔到容器內
COPY requirements.txt .

# 安裝專案依賴
RUN pip install --no-cache-dir -r requirements.txt

# 複製整個應用程式原始碼到容器中
COPY . .

# 若專案使用 .env 檔，Docker 在建置時不會自動帶入，
# 建議在執行容器時使用 -e 或 docker-compose 的環境變數傳入
#
# 例如：docker run -d -p 5000:5000 --env-file .env my-flask-app

# 對外開放 Flask 預設埠（可根據實際情況調整）
EXPOSE 5000

# 設定啟動指令（假設你有 app.py 並在其中宣告了 app）
CMD ["python", "api.py"]
