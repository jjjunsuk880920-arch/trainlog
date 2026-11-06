# TrainLog – 훈련 분석 (개인용)

## 가장 쉬운 방법: IGPSPORT 직접 연동 (무료)
Render 환경변수에 `SITE_PASSWORD`, `IGP_USER`(IGPSPORT 이메일), `IGP_PASSWORD`, `IGP_REGION`(international 또는 china)을 넣으면 끝이에요. 비공식 방식이라 IGPSPORT가 바꾸면 멈출 수 있어요. 스트라바는 2026년 6월부터 API에 유료 구독이 필요해서, 구독할 때만 아래 방법을 쓰세요.


스트라바에 올라온 내 운동을 자동으로 가져와 체력(CTL)·피로(ATL)·컨디션(TSB) 곡선을 보여줍니다.

## 1. 스트라바 API 앱 만들기 (한 번만)
1. https://www.strava.com/settings/api 접속 → 앱 만들기 (이름·웹사이트는 아무거나)
2. **Authorization Callback Domain** 칸에 Render 주소의 도메인만 입력 (예: `trainlog.onrender.com`, https:// 제외)
3. 화면에 나오는 **Client ID**, **Client Secret** 을 메모

## 2. 배포
1. GitHub에 새 저장소(예: trainlog)를 만들어 이 폴더 파일을 모두 올립니다. (GPXTo와 따로)
2. Render → New → Web Service → 그 저장소 선택 (Free)
3. Environment(환경변수) 입력: `SITE_PASSWORD`(사이트 비밀번호), `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`
   (`STRAVA_REFRESH_TOKEN` 은 아직 비워두세요)

## 3. 스트라바 승인 (한 번만)
1. 배포된 주소 접속 → SITE_PASSWORD 입력 (아직 "설정 안 됨" 오류가 나도 정상)
2. 같은 브라우저에서 `주소/strava/connect` 접속 → 스트라바에서 "승인"
3. 화면에 나온 긴 값을 복사해서 Render 환경변수 `STRAVA_REFRESH_TOKEN` 에 붙여넣고 저장 → 자동 재배포
4. 사이트 새로고침 → 자동 동기화. 폰 크롬 메뉴 → "홈 화면에 추가"

## 주의
- 개인용입니다. 다른 사람에게 공유하는 서비스는 스트라바 약관상 제한이 있어요.
- 토큰은 승인하면 이 기기 브라우저에 자동 저장돼요(Render에 붙여넣을 필요 없음). Client Secret은 Render 환경변수에만 넣고 채팅·GitHub에 올리지 마세요.
- 운동 기록은 브라우저(IndexedDB)에 저장돼요. 기기를 바꾸면 처음부터 다시 동기화돼요.
- Render 무료는 잠들었다 깨는 데 30~60초 걸려요.
- 스트라바 호출 한도: 15분 100회. 처음 60개 동기화는 운동당 2회 호출이라 한도에 걸리면 15분 뒤 새로고침하면 이어서 가져와요.
- 화면 설정에 FTP(약 300W)를 입력해야 부하가 계산돼요. 러닝은 LTHR(심박)을 입력하세요.
