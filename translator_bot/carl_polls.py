"""Strict Carl quick-poll recognition; voting remains on the original post."""
from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from .languages import LANGUAGES

CARL_ID = 235148962103951360
POLL_POLICY = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
OPTION = re.compile(r'^([1-9]\ufe0f?\u20e3|🔟):[ \t]*(\S[^\n]*)$')


@dataclass(frozen=True)
class CarlPoll:
    question: str
    choices: tuple[tuple[str, str], ...]

    @property
    def text(self):
        return '\n'.join((self.question, *(text for _, text in self.choices)))

    def signature(self, languages, policy):
        return hashlib.sha256(json.dumps([self.question,self.choices,languages,policy,POLL_POLICY],
            ensure_ascii=False).encode()).hexdigest()

    def render(self, translated, target, url):
        lines = translated.split('\n')
        question_lines = self.question.count('\n')+1
        if len(lines) != question_lines+len(self.choices):
            raise ValueError('Poll translation changed question/answer boundaries')
        question = '\n'.join(lines[:question_lines])
        choices = '\n'.join(f'{emoji}: {text}' for (emoji,_),text in zip(self.choices,lines[question_lines:]))
        heading = 'Перевод опроса' if target=='ru' else 'Poll translation'
        vote = 'Голосуйте в оригинальном опросе' if target=='ru' else 'Vote on the original poll'
        return f'**{heading} · {LANGUAGES[target]}**\n{question}\n\n{choices}\n\n[{vote}]({url})'


def parse_carl_poll(message):
    author = getattr(message,'author',None)
    if (getattr(author,'id',None)!=CARL_ID or not getattr(author,'bot',False)
            or getattr(message,'webhook_id',None) not in (None,CARL_ID)):
        return None
    match = re.fullmatch(r'\*\*[^*\r\n]{1,80}\*\* asks:[ \t]*(\S[\s\S]*?)\n[ \t]*\n([\s\S]+)',
        getattr(message,'content','').strip())
    if not match:
        return None
    choices=[]
    for line in match[2].split('\n'):
        option=OPTION.fullmatch(line.rstrip())
        if not option:
            return None
        emoji,text=option.groups()
        number=10 if emoji=='🔟' else int(emoji[0])
        if number!=len(choices)+1:
            return None
        choices.append((emoji,text.strip()))
    return CarlPoll(match[1].strip(),tuple(choices)) if 2<=len(choices)<=10 else None
