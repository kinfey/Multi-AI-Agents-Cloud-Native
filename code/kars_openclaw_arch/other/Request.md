请做财经短视频解决方案，

1. 一个财金的解决方案，一个基于 kars 的 openclaw agent 结构，

- 需要每天早上 8点 获取最新的 10 条中美股市动态，
- 每条股市动态用 MAI-image-2.5 Pro 生成 1 张 9:16 的图，图的风格要专业的财经风格，风格一致，要专业独特视觉，每张图要配合好主题和一些文字，并且生成语音脚本(4-5句话），注意要生成封面，和结束封面
- 用 MAI speech 进行中文配音，最后要用 ffmpeg 合成一个视频 保存在 azure blob storage 上
- 注意每天都要保存在 yymmdd 的文件夹，并且生成的图片要放在 yymmdd/imgs/ 上按照顺序cover.png, 01.png , 02.png ......end.png，还有 yymmdd/audio/ 上按照顺序 conver.wav,01.wav,02.wav...end.wav, 最后生成的视频 yymmdd/video/final.mp4

2. 再创建一个 web app 的 container apps 的短视频列表创建一个 视频podcast 风格的页面，完成一个 spa 前端解决方案，加载生成的视频，可以直接点播，给 like ，给星，直接采用 JS + CSS3 + HTML5 


3. 参考 

- MAI-IMAGE_Pro-2.5

curl -X POST "https://<foundry-account>.services.ai.azure.com/mai/v1/images/generations" \
-H "Content-Type: application/json" \
-H "api-key: $AZURE_API_KEY" \
-d '{
    "prompt": "A photograph of a red fox in an autumn forest",
    "width": 1024,
    "height": 1024,
    "model": "MAI-Image-2.5-Pro"
}' | jq -r '.data[0].b64_json' | base64 --decode > generated_image.png

- MAI-Voice-2


import azure.cognitiveservices.speech as speechsdk

endpoint_url = "https://<speech-account>.cognitiveservices.azure.com/"

from urllib.parse import urlparse

parsed = urlparse(endpoint_url)
base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

speech_key = "<your-api-key>"
speech_config = speechsdk.SpeechConfig(subscription=speech_key, endpoint=base_endpoint)
speech_config.speech_synthesis_voice_name = "en-US-Ethan:MAI-Voice-2"

# use the default speaker as audio output.
speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)

text = "Hello, welcome to Azure AI Foundry!"

result = speech_synthesizer.speak_text_async(text).get()

# Check result
if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
    print("Speech synthesized for text [{}]".format(text))
elif result.reason == speechsdk.ResultReason.Canceled:
    cancellation_details = result.cancellation_details
    print("Speech synthesis canceled: {}".format(cancellation_details.reason))
    if cancellation_details.reason == speechsdk.CancellationReason.Error:
        print("Error details: {}".format(cancellation_details.error_details))

AZURE_SPEECH_ENDPOINT="https://<speech-account>.cognitiveservices.azure.com/"
AZURE_SPEECH_KEY="<set-in-azd-or-key-vault;never-commit>"
AZURE_RESOURCE_ID="/subscriptions/<subscription-id>/resourceGroups/<resource-group-name>/providers/Microsoft.CognitiveServices/accounts/<speech-account>"

- 使用 Microsoft Foundry Models - gpt-5.5 

Endpoint=https://<foundry-account>.services.ai.azure.com/api/projects/<foundry-project-name>

- 请参考 Kars 上的 

https://github.com/Azure/kars/tree/main/examples 中 openclaw 的例子

- 最后还要做 redteam 测试

https://github.com/Azure/kars/blob/main/docs/security/red-team.md ，并创建一个 container apps 来展示测试

- Openclaw 多 Agents 的方式

https://docs.openclaw.ai/concepts/agent-workspace

https://docs.openclaw.ai/concepts/main-session


- 总共 3 个 Agent 生成

  Media-Claw-Agent 从资料挖掘到图片生成再到脚本，然后合成，保存

  Media-App-Agent 前端展示

  Media-Testing-Agent 用 Red-Team Test Media-Claw-Agent

  注意三个 agent 都在 agents ，有三个文件夹

  media-claw-agent / media-app-agent / media-testing-agent

- 使用 KARS 部署到 azure





