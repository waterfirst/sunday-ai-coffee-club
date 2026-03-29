#!/usr/bin/env python3
"""
AI 모임 팟캐스트 생성기
- Gemini: 대본 생성 (두 사람 대화형)
- GPT TTS: 음성 합성 (두 목소리)
- ffmpeg: 오디오 결합
"""

import os, json, time, subprocess, re, sys
from pathlib import Path
from datetime import datetime
import requests

# === 설정 ===
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "podcasts"
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR = BASE_DIR / "podcasts" / "temp"
TEMP_DIR.mkdir(exist_ok=True)

# GPT TTS 목소리 설정 (호스트 두 명)
HOST_A_VOICE = "nova"      # 여성, 따뜻하고 자연스러운
HOST_B_VOICE = "onyx"      # 남성, 차분하고 깊은

def gemini_call(prompt, temperature=0.8, max_tokens=8000):
    """Gemini API 호출"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def generate_podcast_script(summary_text, topic_name):
    """세미나 요약에서 팟캐스트 대본 생성"""
    prompt = f"""당신은 한국어 기술 팟캐스트 "일요 AI 커피 클럽"의 대본 작가입니다.

아래 세미나 요약을 바탕으로, 두 명의 호스트가 자연스럽게 대화하는 팟캐스트 대본을 만들어주세요.

## 호스트 설정
- **진행자A** (여성): 호기심 많고 질문을 잘 던지는 진행자. 복잡한 개념을 쉽게 풀어 설명하려 함.
- **진행자B** (남성): 기술 전문가 역할. 깊이 있는 설명과 투자 인사이트를 제공.

## 대본 규칙
1. 자연스러운 한국어 구어체 사용 (예: "~거든요", "~잖아요", "맞아요", "그렇죠")
2. 총 15~20개 대화 턴 (약 8~10분 분량)
3. 시작: 인사 + 오늘 주제 소개
4. 중간: 핵심 내용 3~4가지를 자연스럽게 풀어감
5. 끝: 투자/사업 시사점 + 마무리 인사
6. 각 대사는 2~4문장으로 짧게 (TTS에 적합하게)
7. 감탄사, 맞장구 포함 ("오~", "아하", "맞아요", "와 정말요?")

## 출력 형식 (JSON)
정확히 이 JSON 형식으로만 출력하세요. 다른 텍스트 없이 JSON만:
```json
[
  {{"speaker": "A", "text": "대사 내용"}},
  {{"speaker": "B", "text": "대사 내용"}},
  ...
]
```

## 세미나 요약 내용
{summary_text}

## 주제: {topic_name}

JSON 대본을 생성하세요:"""

    result = gemini_call(prompt, temperature=0.85, max_tokens=8000)

    # JSON 추출
    json_match = re.search(r'\[[\s\S]*\]', result)
    if json_match:
        script = json.loads(json_match.group())
    else:
        raise ValueError(f"JSON 파싱 실패: {result[:500]}")

    return script


def tts_openai(text, voice, output_path):
    """OpenAI TTS API로 음성 생성"""
    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini-tts",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "speed": 1.0,
        "instructions": "Speak naturally in Korean like a podcast host. Use conversational tone with natural pauses and emphasis."
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()

    with open(output_path, 'wb') as f:
        f.write(resp.content)

    return output_path


def combine_audio_segments(segment_files, output_path):
    """ffmpeg로 오디오 세그먼트 결합"""
    # concat 파일 리스트 생성
    concat_file = TEMP_DIR / "concat_list.txt"
    with open(concat_file, 'w') as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-codec:a", "libmp3lame", "-b:a", "128k",
        "-ar", "44100", "-ac", "1",
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오류: {result.stderr}")

    return output_path


def add_silence(duration_ms, output_path):
    """무음 구간 생성"""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono",
        "-t", str(duration_ms / 1000),
        "-codec:a", "libmp3lame", "-b:a", "128k",
        str(output_path)
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)
    return output_path


def generate_podcast(summary_path, topic_name, date_str=None):
    """전체 팟캐스트 생성 파이프라인"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    print(f"\n{'='*50}")
    print(f"🎙️ 팟캐스트 생성 시작: {topic_name}")
    print(f"{'='*50}")

    # 1. 요약 읽기
    print("\n📖 세미나 요약 읽는 중...")
    with open(summary_path, 'r', encoding='utf-8') as f:
        summary_text = f.read()

    # 2. 대본 생성
    print("📝 팟캐스트 대본 생성 중 (Gemini)...")
    script = generate_podcast_script(summary_text, topic_name)
    print(f"   → {len(script)}개 대화 턴 생성 완료")

    # 대본 저장
    script_path = OUTPUT_DIR / f"podcast_script_{date_str}.json"
    with open(script_path, 'w', encoding='utf-8') as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # 3. TTS 음성 합성
    print("🔊 음성 합성 중 (GPT TTS)...")
    segment_files = []

    # 짧은 무음 (대화 사이 간격)
    silence_path = TEMP_DIR / "silence_500ms.mp3"
    add_silence(500, silence_path)

    for i, turn in enumerate(script):
        speaker = turn["speaker"]
        text = turn["text"]
        voice = HOST_A_VOICE if speaker == "A" else HOST_B_VOICE

        seg_path = TEMP_DIR / f"seg_{i:03d}_{speaker}.mp3"
        print(f"   [{i+1}/{len(script)}] 진행자{speaker}: {text[:30]}...")

        try:
            tts_openai(text, voice, seg_path)
            segment_files.append(str(seg_path))
            # 대화 사이에 짧은 무음 삽입
            segment_files.append(str(silence_path))
        except Exception as e:
            print(f"   ⚠️ TTS 실패 (턴 {i}): {e}")
            continue

        # rate limit 방지
        time.sleep(0.3)

    if not segment_files:
        raise RuntimeError("음성 세그먼트 생성 실패")

    # 4. 오디오 결합
    print("🎵 오디오 결합 중 (ffmpeg)...")
    output_filename = f"podcast_{date_str}_{topic_name.replace(' ', '_')}.mp3"
    output_path = OUTPUT_DIR / output_filename
    combine_audio_segments(segment_files, output_path)

    # 파일 크기 확인
    file_size = os.path.getsize(output_path)
    print(f"\n✅ 팟캐스트 생성 완료!")
    print(f"   파일: {output_path}")
    print(f"   크기: {file_size / 1024 / 1024:.1f} MB")
    print(f"   대본: {script_path}")

    # temp 정리
    for f in TEMP_DIR.glob("seg_*"):
        f.unlink()

    return str(output_path), str(script_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 모임 팟캐스트 생성기")
    parser.add_argument("--summary", required=True, help="요약 마크다운 파일 경로")
    parser.add_argument("--topic", required=True, help="주제명")
    parser.add_argument("--date", default=None, help="날짜 (YYYYMMDD)")
    parser.add_argument("--send", action="store_true", help="텔레그램 전송")
    args = parser.parse_args()

    audio_path, script_path = generate_podcast(args.summary, args.topic, args.date)

    if args.send:
        print("\n📱 텔레그램 전송 중...")
        os.system(f'"/usr/local/bin/cokacdir" --sendfile {audio_path} --chat 5767743818 --key 2d09e87522bd6d28')
        print("✅ 전송 완료!")
