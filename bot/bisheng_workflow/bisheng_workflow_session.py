from bot.session_manager import Session
from common.log import logger

"""
    e.g.  [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Who won the world series in 2020?"},
        {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
        {"role": "user", "content": "Where was it played?"}
    ]
"""


class BishengWorkflowSession(Session):
    def __init__(self, session_id, system_prompt=None, model=None):
        super().__init__(session_id, system_prompt)
        self.model = model
        self.workflow_session_id = None
        self.input_node_id = None
        self.message_id = None
        self.schema_fields = []
        self.reset()

    def reset(self):
        super().reset()
        self.workflow_session_id = None
        self.input_node_id = None
        self.message_id = None
        self.schema_fields = []

    def set_workflow_session(self, workflow_session_id, input_node_id, message_id, schema_fields=None):
        self.workflow_session_id = workflow_session_id
        self.input_node_id = input_node_id
        self.message_id = message_id
        if schema_fields:
            self.schema_fields = schema_fields

    def reset_workflow_session(self):
        self.workflow_session_id = None
        self.input_node_id = None
        self.message_id = None

    def get_workflow_session(self):
        return self.workflow_session_id, self.input_node_id, self.message_id

    def get_schema_fields(self):
        return self.schema_fields

    def discard_exceeding(self, max_tokens, cur_tokens=None):
        precise = True
        try:
            cur_tokens = self.calc_tokens()
        except Exception as e:
            precise = False
            if cur_tokens is None:
                raise e
            logger.debug("Exception when counting tokens precisely for query: {}".format(e))
        while cur_tokens > max_tokens:
            if len(self.messages) > 2:
                self.messages.pop(1)
            elif len(self.messages) == 2 and self.messages[1]["role"] == "assistant":
                self.messages.pop(1)
                if precise:
                    cur_tokens = self.calc_tokens()
                else:
                    cur_tokens = cur_tokens - max_tokens
                break
            elif len(self.messages) == 2 and self.messages[1]["role"] == "user":
                logger.warn("user message exceed max_tokens. total_tokens={}".format(cur_tokens))
                break
            else:
                logger.debug("max_tokens={}, total_tokens={}, len(messages)={}".format(max_tokens, cur_tokens, len(self.messages)))
                break
            if precise:
                cur_tokens = self.calc_tokens()
            else:
                cur_tokens = cur_tokens - max_tokens
        return cur_tokens

    def calc_tokens(self):
        return num_tokens_by_character(self.messages)


def num_tokens_by_character(messages):
    """Returns the number of tokens used by a list of messages."""
    tokens = 0
    for msg in messages:
        tokens += len(msg["content"])
    return tokens 