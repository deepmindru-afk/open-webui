import os
import json
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

# Интеграция внутренних механизмов аутентификации Open WebUI
from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel

# Библиотека генерации токенов LiveKit
from livekit.api import (
    AccessToken,
    VideoGrants,
    RoomConfiguration,
    RoomAgentDispatch
)

router = APIRouter()

# Определение Pydantic-схем для валидации запросов и ответов
class LiveKitConnectionRequest(BaseModel):
    chat_id: str
    room_name: Optional[str] = None
    agent_name: Optional[str] = "open-webui-voice-agent"

class LiveKitConnectionResponse(BaseModel):
    server_url: str
    room_name: str
    access_token: str

@router.post("/generate-token", response_model=LiveKitConnectionResponse)
async def create_livekit_session_token(
    request: Request,
    payload: LiveKitConnectionRequest,
    user: UserModel = Depends(get_verified_user)
):
    """
    Эндпоинт для выдачи токенов LiveKit авторизованным пользователям Open WebUI.
    Инкапсулирует конфигурацию комнаты и метаданные диспетчеризации агента.
    """
    # Извлечение конфигурации из переменных окружения. 
    # В Open WebUI они обычно считываются через config.py или env.py
    lk_url = os.environ.get("LIVEKIT_URL")
    lk_api_key = os.environ.get("LIVEKIT_API_KEY")
    lk_api_secret = os.environ.get("LIVEKIT_API_SECRET")

    if not all([lk_url, lk_api_key, lk_api_secret]):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Внутренняя ошибка конфигурации: параметры сервера LiveKit не заданы."
        )

    # Формирование идентификаторов
    room_id = payload.room_name or f"owui-chat-{payload.chat_id}-{uuid.uuid4().hex[:8]}"
    participant_identity = f"user-{user.id}"
    participant_name = user.name if user.name else user.email

    # Сериализация контекста для рабочего узла агента
    # Эта информация будет доступна в Python-процессе агента через ctx.job.metadata
    job_metadata = json.dumps({
        "chat_id": payload.chat_id,
        "user_id": user.id,
        "user_name": participant_name,
        "user_role": user.role
    })

    try:
        # Конструирование JWT токена с грантами и конфигурацией явной диспетчеризации
        token_builder = AccessToken(lk_api_key, lk_api_secret)
        token_builder.with_identity(participant_identity)
        token_builder.with_name(participant_name)
        
        # Предоставление прав на подключение и публикацию аудио
        token_builder.with_grants(VideoGrants(
            room_join=True,
            room=room_id,
            can_publish=True,
            can_subscribe=True
        ))

        # Конфигурация диспетчеризации: агент запустится при создании комнаты
        # Использование RoomConfiguration и RoomAgentDispatch гарантирует, 
        # что именно этот конкретный токен активирует агента.
        room_config = RoomConfiguration(
            agents=[
                RoomAgentDispatch(
                    agent_name=payload.agent_name,
                    metadata=job_metadata
                )
            ]
        )
        token_builder.with_room_config(room_config)

        # Подписание токена
        jwt_token = token_builder.to_jwt()

        return LiveKitConnectionResponse(
            server_url=lk_url,
            room_name=room_id,
            access_token=jwt_token
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка криптографического подписания токена LiveKit: {str(e)}"
        )