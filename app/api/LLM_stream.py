import uuid
import aiohttp
import requests
from .auth_util import gen_sign_headers
import json
import os

import logging
logger=logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class VivoGPTAPI:
    def __init__(self,
                 app_id=os.getenv("APP_ID"), 
                 app_key=os.getenv("APP_KEY"), 
                 model='vivo-BlueLM-TB-Pro', 
                 domain='api-ai.vivo.com.cn'
        ):
        self.app_id = app_id
        self.app_key = app_key
        self.model = model
        self.domain = domain
        self.uri = '/vivogpt/completions/stream'
        self.method = 'POST'
        self.stop_signal = False
        self._aio_session = None
    def build_headers(self, params):
        headers = gen_sign_headers(self.app_id, self.app_key, self.method, self.uri, params)
        headers['Content-Type'] = 'application/json'
        return headers

    def stop(self):
        self.stop_signal = True

    def stream_sync(self, prompt: str):
        self.stop_signal = False
        params = {'requestId': str(uuid.uuid4())}
        headers = self.build_headers(params)
        data = {
            'prompt': prompt,
            'sessionId': str(uuid.uuid4()),
            'model': self.model
        }
        url = f'http://{self.domain}{self.uri}'

        try:
            response = requests.post(url, headers=headers, json=data, params=params, stream=True, timeout=30)
            response.raise_for_status()
        except Exception as e:
            logger.info(f"[Sync] 请求异常：{e}")
            return
        for line in response.iter_lines():
            if self.stop_signal:
                logger.info("[Sync] 中断信号触发，终止输出")
                break
            if line:
                line:str=line.decode('utf-8', errors='ignore')
                idx=line.find('{')
                if idx!=-1:
                    json_line=line[idx:]
                elif line=='event:close':
                    logger.debug("[Sync] event close")
                    return
                elif line=='event:antispam':
                    logger.warning("[Sync] antispam occurred")
                    return
                elif line=='event:error':
                    logger.warning("[Sync] error occurred")
                    return
                else:
                    json_line=line
                try:
                    data:dict=json.loads(json_line)
                except Exception as e:
                    logger.error(f"[Sync] Error load json string:{e}, repr(line):{repr(json_line)}")
                    raise
                message=data.get("message")
                yield message

    async def stream_async(self, prompt: str):
        self.stop_signal = False
        params = {'requestId': str(uuid.uuid4())}
        headers = self.build_headers(params)
        data = {
            'prompt': prompt,
            'sessionId': str(uuid.uuid4()),
            'model': self.model
        }
        url = f'http://{self.domain}{self.uri}'

        if self._aio_session is None:
            self._aio_session = aiohttp.ClientSession()

        try:
            async with self._aio_session.post(url, headers=headers, json=data, params=params, timeout=30) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[Async] 请求失败，状态码: {resp.status}，内容: {text}")
                    return
                async for line in resp.content:
                    if self.stop_signal:
                        logger.error("[Async] 中断信号触发，终止输出")
                        break
                    if line:
                        line:str=line.decode('utf-8', errors='ignore')
                        idx=line.find('{')
                        if idx!=-1:
                            json_line=line[idx:]
                        else:
                            continue
                        data:dict=json.loads(json_line)
                        message=data.get("message")
                        yield message
        except Exception as e:
            raise Exception(f"[Async] 异步请求异常：{e}")
    async def close_async(self):
        if self._aio_session:
            await self._aio_session.close()
            self._aio_session = None

if __name__=="__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import os
    APP_ID,APP_KEY=os.getenv("APP_ID"),os.getenv("APP_KEY")
    # print(APP_KEY,APP_ID)

    # 同步调用:
    client = VivoGPTAPI(APP_ID, APP_KEY)
    s=''
    for chunk in client.stream_sync(prompt="写一首春天的诗"):
        s+=chunk
        print(chunk, end="")
        if "花开" in chunk:
            client.stop() #中断机制
    print('\n',''.center(50,'='),'\n')
    
    # 异步流式调用
    import asyncio
    async def main():
        client = VivoGPTAPI()
        async for chunk in client.stream_async(prompt="写一首春天的诗"):
            print(chunk, end="")
            if "银河" in chunk:
                client.stop()
        await client.close_async()

    asyncio.run(main())

