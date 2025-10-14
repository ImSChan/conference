# Dooray Dialog Sample

- /dooray/command: 슬래시 커맨드 요청 → Dooray Dialog API 호출
- /dooray/actions: dialog_submission 검증 및 채널 공지

## ENV (선택)
- DOORAY_VERIFY_TOKEN=your-secret-token

## Local
pip install -r requirements.txt
uvicorn api.index:app --reload

## Deploy
vercel
