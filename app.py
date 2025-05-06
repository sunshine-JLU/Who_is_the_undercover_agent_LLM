import random
import time
import threading
from queue import Queue
from flask import Flask, render_template, request, jsonify, Response
import requests
from typing import List, Dict
import json
app = Flask(__name__)

# API配置
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
API_KEY = "********"#填写你的硅基流动秘钥(api-key)
#下面是页面可以选择的模型，需要到https://cloud.siliconflow.cn/models上去把对应模型的名字按照下面格式填入；
AVAILABLE_MODELS = ["Qwen/Qwen3-32B", "Qwen/QwQ-32B", "Pro/deepseek-ai/DeepSeek-V3","Qwen/Qwen2.5-7B-Instruct","Qwen/Qwen3-8B","Qwen/Qwen3-14B"]


class Player:
    def __init__(self, player_id: int, model: str):
        self.id = player_id
        self.model = model
        self.role = None  # "civilian" or "undercover"
        self.word = None
        self.alive = True
        self.votes_received = 0

    def __str__(self):
        return f"Player {self.id} ({self.model})"


class UndercoverGame:
    def __init__(self):
        self.players: List[Player] = []
        self.civilian_word = ""
        self.undercover_word = ""
        self.current_round = 0
        self.game_over = False
        self.message_queue = Queue()
        self.game_thread = None

    def initialize_game(self, player_models: List[str],
                        civilian_word: str, undercover_word: str,
                        undercover_indices: List[int]):
        self.players = []
        self.current_round = 0
        self.game_over = False

        # 创建玩家
        for i, model in enumerate(player_models):
            self.players.append(Player(i + 1, model))

        self.civilian_word = civilian_word
        self.undercover_word = undercover_word

        # 分配角色
        for i, player in enumerate(self.players):
            if i + 1 in undercover_indices:
                player.role = "undercover"
                player.word = undercover_word
            else:
                player.role = "civilian"
                player.word = civilian_word

        self._broadcast("游戏初始化完成", is_system=True)
        self._broadcast(f"平民词语: {civilian_word}, 卧底词语: {undercover_word}", is_system=True)

    def _broadcast(self, message: str, is_system=True, player_id=None):
        """将消息放入队列"""
        msg = {
            "type": "system" if is_system else "player",
            "content": message,
            "player_id": player_id,
            "timestamp": time.time()
        }
        self.message_queue.put(msg)

    def _call_model_api(self, player: Player, messages: List[Dict]) -> str:
        """调用大模型API"""
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": player.model,
            "messages": messages,
            "stream": False,
            "max_tokens": 256,
            "temperature": 0.7
        }

        try:
            response = requests.post(API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            self._broadcast(f"调用{player}的API时出错: {str(e)}", is_system=True)
            return "我不知道该说什么。"

    def _get_alive_players(self) -> List[Player]:
        return [p for p in self.players if p.alive]

    def _get_player_description(self) -> str:
        alive_players = self._get_alive_players()
        return "、".join([f"玩家{p.id}({p.model})" for p in alive_players])

    def play_round(self):
        self.current_round += 1
        self._broadcast(f"=== 第 {self.current_round} 轮开始 ===", is_system=True)

        # 重置投票计数
        for player in self.players:
            player.votes_received = 0

        # 1. 发言阶段
        alive_players = self._get_alive_players()
        for player in alive_players:
            prompt = f"""你正在玩"谁是卧底"游戏，当前是第{self.current_round}轮。你的身份是{player.role}，你的词语是"{player.word}"。
当前存活玩家: {self._get_player_description()}
请用一句话描述你的词语，不要直接说出词语本身。你的描述应该让同伴认出你，但不要让卧底发现你的真实词语。"""

            messages = [
                {"role": "system", "content": "你是一个游戏玩家，正在参与'谁是卧底'游戏。"},
                {"role": "user", "content": prompt}
            ]

            response = self._call_model_api(player, messages)
            description = response.strip()

            self._broadcast(description, is_system=False, player_id=player.id)
            time.sleep(1)

        # 2. 投票阶段
        self._broadcast("开始投票阶段...", is_system=True)

        for voter in alive_players:
            prompt = f"""现在是投票阶段，请根据以下玩家的发言，投票选出你认为的卧底。
当前存活玩家: {self._get_player_description()}
游戏历史:
"""
            # 添加历史发言
            for msg in list(self.message_queue.queue)[-len(alive_players):]:
                if msg["type"] == "player":
                    prompt += f"玩家{msg['player_id']}说: {msg['content']}\n"

            prompt += f"\n你是玩家{voter.id}，请直接回答你要投票的玩家编号(只输出数字，如'1')。"

            messages = [
                {"role": "system", "content": "你正在参与'谁是卧底'游戏的投票阶段。"},
                {"role": "user", "content": prompt}
            ]

            response = self._call_model_api(voter, messages)

            try:
                voted_id = int(response.strip())
                voted_player = next((p for p in self.players if p.id == voted_id and p.alive), None)
                if voted_player:
                    voted_player.votes_received += 1
                    self._broadcast(f"玩家 {voter.id} 投票给玩家 {voted_id}", is_system=True)
                else:
                    self._broadcast(f"玩家 {voter.id} 的投票无效: {response}", is_system=True)
            except ValueError:
                self._broadcast(f"玩家 {voter.id} 的投票无效: {response}", is_system=True)

        # 3. 处理投票结果
        max_votes = max(p.votes_received for p in self.players)
        voted_out = [p for p in self.players if p.votes_received == max_votes and p.alive]

        if len(voted_out) == 1:
            voted_out[0].alive = False
            self._broadcast(f"玩家 {voted_out[0].id} 被投票出局，身份是 {voted_out[0].role}", is_system=True)

            # 检查游戏是否结束
            undercover_alive = sum(1 for p in self.players if p.role == "undercover" and p.alive)
            civilian_alive = sum(1 for p in self.players if p.role == "civilian" and p.alive)

            if undercover_alive == 0:
                self._broadcast("所有卧底已被找出，平民获胜！", is_system=True)
                self.game_over = True
            elif undercover_alive >= civilian_alive:
                self._broadcast("卧底人数占优，卧底获胜！", is_system=True)
                self.game_over = True
        else:
            self._broadcast("投票平局，没有人被淘汰", is_system=True)

        if self.current_round >= 5 and not self.game_over:
            self._broadcast("游戏轮次已达上限，卧底获胜！", is_system=True)
            self.game_over = True

    def start_game(self):
        while not self.game_over:
            self.play_round()
            time.sleep(2)

        self._broadcast("=== 游戏结束 ===", is_system=True)
        self._broadcast(f"平民词语: {self.civilian_word}, 卧底词语: {self.undercover_word}", is_system=True)
        for player in self.players:
            self._broadcast(f"玩家 {player.id}: 身份 {player.role}, 词语 {player.word}", is_system=True)


game_instance = UndercoverGame()


@app.route('/')
def index():
    return render_template('index.html', models=AVAILABLE_MODELS)


@app.route('/start_game', methods=['POST'])
def start_game():
    if game_instance.game_thread and game_instance.game_thread.is_alive():
        return jsonify({"error": "游戏正在进行中"}), 400

    data = request.json
    player_models = data['player_models']
    civilian_word = data['civilian_word']
    undercover_word = data['undercover_word']
    undercover_indices = [int(i) for i in data['undercover_indices']]

    game_instance.initialize_game(
        player_models=player_models,
        civilian_word=civilian_word,
        undercover_word=undercover_word,
        undercover_indices=undercover_indices
    )

    game_instance.game_thread = threading.Thread(target=game_instance.start_game)
    game_instance.game_thread.start()

    return jsonify({"status": "游戏已开始"})


@app.route('/stream')
def stream():
    def event_stream():
        last_id = 0
        while True:
            if not game_instance.message_queue.empty():
                msg = game_instance.message_queue.get()
                player = next((p for p in game_instance.players if p.id == msg.get('player_id')), None)
                model = player.model if player else None

                data = {
                    "type": msg["type"],
                    "content": msg["content"],
                    "player_id": msg.get("player_id"),
                    "model": model,
                    "timestamp": msg["timestamp"]
                }
                yield f"data: {json.dumps(data)}\n\n"
                last_id += 1
            elif game_instance.game_over and game_instance.message_queue.empty():
                break
            else:
                time.sleep(0.1)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route('/game_status')
def game_status():
    players_info = []
    for player in game_instance.players:
        players_info.append({
            "id": player.id,
            "model": player.model,
            "role": player.role,
            "word": player.word,
            "alive": player.alive,
            "votes_received": player.votes_received
        })

    return jsonify({
        "current_round": game_instance.current_round,
        "game_over": game_instance.game_over,
        "civilian_word": game_instance.civilian_word,
        "undercover_word": game_instance.undercover_word,
        "players": players_info
    })


if __name__ == '__main__':
    app.run(debug=True, threaded=True)
