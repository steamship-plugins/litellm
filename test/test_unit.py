import inspect
import json
import os
import pathlib

import openai
import pytest
import toml
from litellm import AuthenticationError
from steamship import Block, Tag, MimeTypes, SteamshipError, File, Steamship
from steamship.data.tags.tag_constants import TagKind, RoleTag, TagValueKey, ChatTag
from steamship.plugin.inputs.raw_block_and_tag_plugin_input import (
    RawBlockAndTagPluginInput,
)
from steamship.plugin.inputs.raw_block_and_tag_plugin_input_with_preallocated_blocks import (
    RawBlockAndTagPluginInputWithPreallocatedBlocks,
)
from steamship.plugin.outputs.plugin_output import UsageReport, OperationUnit, OperationType
from steamship.plugin.request import PluginRequest

from src.api import LiteLLMPlugin

LLAMA = "replicate/llama-2-70b-chat:2796ee9483c3fd7aa2e171d38f4ca12251a30609463dcfd4cd76703f22e96cdf"
FUNCTION_MODEL_PARAMS = ["", "gpt-4-32k"]
MODEL_PARAMS = FUNCTION_MODEL_PARAMS + [LLAMA]

COUNT_SYSTEM_PROMPT = "You are an assistant who loves to count.  You do not include text in your responses, only numbers."
COUNT_USER_PROMPT = "Continue this series, responding only with the next 4 numbers: 1 2 3 4"

@pytest.fixture()
def envreset():
    original = os.environ.copy().keys()
    yield None
    new = os.environ.keys()
    new_keys = new - original
    for key in new_keys:
        del os.environ[key]


@pytest.mark.parametrize("model", MODEL_PARAMS)
def test_generator(model: str, envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={"n": 1, "model": model})

        blocks = [
            Block(
                text=COUNT_SYSTEM_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text=COUNT_USER_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        usage, new_blocks = run_test_streaming(client, litellm, blocks, options={})
        assert len(new_blocks) == 1
        for block in new_blocks:
            assert block.text.strip().startswith("5 6 7 8")

        assert usage is not None
        assert len(usage) == 1


# TODO: This appears to be a bug?  Stopwords don't appear to work for Llama but they are a feature on that model on replicate.
# @pytest.mark.parametrize("model", MODEL_PARAMS)
def test_stopwords(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={})

        blocks = [
            Block(
                text=COUNT_SYSTEM_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text=COUNT_USER_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        _, new_blocks = run_test_streaming(
            client, litellm, blocks=blocks, options={"stop": "6"}
        )
        assert len(new_blocks) == 1
        assert new_blocks[0].text.strip() == "5"


def test_functions(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={})

        blocks = [
            Block(
                text="You are a helpful AI assistant.",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text="Search for the weather of today in Berlin",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        _, new_blocks = run_test_streaming(
            client,
            litellm,
            blocks=blocks,
            options={
                "functions": [
                    {
                        "name": "Search",
                        "description": "useful for when you need to answer questions about current events. You should ask targeted questions",
                        "parameters": {
                            "properties": {
                                "query": {"title": "query", "type": "string"}
                            },
                            "required": ["query"],
                            "type": "object",
                        },
                    }
                ]
            },
        )
        assert len(new_blocks) == 1
        assert "function_call" in new_blocks[0].text.strip()
        function_call = json.loads(new_blocks[0].text.strip())
        assert "function_call" in function_call


def test_functions_function_message(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={})

        blocks = [
            Block(
                text="You are a helpful AI assistant.",
                tags=[Tag(kind="role", name="system")],
            ),
            Block(
                text="Who is Vin Diesel's girlfriend?",
                tags=[Tag(kind="role", name="user")],
            ),
            Block(
                text='{"function_call": {"name": "Search", "arguments": "{\\n  \\"__arg1\\": \\"Vin Diesel\'s girlfriend\\"\\n}"}}',
                tags=[Tag(kind="role", name="assistant")],
            ),
            Block(
                text="Paloma Jiménez",
                tags=[
                    Tag(kind="role", name="function"),
                    Tag(kind="name", name="Search"),
                ],
            ),
        ]

        _, new_blocks = run_test_streaming(
            client,
            litellm,
            blocks=blocks,
            options={
                "functions": [
                    {
                        "name": "Search",
                        "description": "useful for when you need to answer questions about current events. You should ask targeted questions",
                        "parameters": {
                            "properties": {
                                "query": {"title": "query", "type": "string"}
                            },
                            "required": ["query"],
                            "type": "object",
                        },
                    }
                ]
            },
        )
        assert len(new_blocks) == 1
        assert new_blocks[0].text is not None
        assert isinstance(new_blocks[0].text, str)
        text = new_blocks[0].text.strip()
        assert "Vin Diesel" in text


@pytest.mark.parametrize("model", MODEL_PARAMS)
def test_default_prompt(model, envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(
            client=client,
            config={
                "model": model,
                "default_system_prompt": "You are very silly and are afraid of numbers. When you see "
                "them you scream: 'YIKES!', and that is your only output.",
                "moderate_output": False,
            },
        )

        blocks = [
            Block(
                text="1 2 3 4",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        _, new_blocks = run_test_streaming(
            client, litellm, blocks=blocks, options={"stop": "6"}
        )
        assert len(new_blocks) == 1
        assert new_blocks[0].text.strip() == "YIKES!"


@pytest.mark.parametrize("model", MODEL_PARAMS)
def test_flagged_prompt(model, envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={"model": model})

        blocks = [
            Block(
                text="fuck fuck fuck fuck fuck fuck fuck yourself",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]
        with pytest.raises(SteamshipError):
            _, _ = run_test_streaming(client, litellm, blocks=blocks, options={})


def test_cant_override_env(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(
            config={}
        )
        with pytest.raises(SteamshipError) as e:
            _, _ = run_test_streaming(client, litellm, blocks=[Block(text="yo")], options={"litellm_env": ""})
        assert "Configured environment (litellm_env) may not be overridden in options" in str(e)


# TODO there appears to be a billing problem with at least replicate here, where it reports $0.00.  This is due to
#  how matching models to billing works in the library.
# @pytest.mark.parametrize("model", MODEL_PARAMS)
def test_streaming_generation(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={})

        blocks = [
            Block(
                text="Tell me a 500 word story about bananas",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        # TODO This test originally specified n=3, but there's a probable bug with litellm and streaming when n > 1
        result_usage, result_blocks = run_test_streaming(
            client, litellm, blocks=blocks, options={"n": 1}
        )
        result_texts = [block.text for block in result_blocks]

        assert len(result_texts) == 1

        assert len(result_usage) == 1
        assert result_usage[0].operation_type == OperationType.RUN
        assert result_usage[0].operation_unit == OperationUnit.UNITS
        assert result_usage[0].operation_amount > 0


def test_streaming_generation_with_moderation(envreset):
    with Steamship.temporary_workspace() as client:
        litellm = LiteLLMPlugin(client=client, config={})

        file = File.create(client, blocks=[
            Block(
                text="Fuck fuck fuck, you fucking fucker!",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ])

        blocks_to_allocate = litellm.determine_output_block_types(
            PluginRequest(data=RawBlockAndTagPluginInput(blocks=file.blocks, options={"n": 1}))
        )

        output_blocks = []
        for block_type_to_allocate in blocks_to_allocate.data.block_types_to_create:
            assert block_type_to_allocate == MimeTypes.TXT.value
            output_blocks.append(
                Block.create(
                    client,
                    file_id=file.id,
                    mime_type=MimeTypes.TXT.value,
                    streaming=True,
                )
            )

        file.refresh()
        assert len(file.blocks) == 2
        assert file.blocks[1].stream_state == "started"

        with pytest.raises(SteamshipError):
            litellm.run(
                PluginRequest(
                    data=RawBlockAndTagPluginInputWithPreallocatedBlocks(
                        blocks=file.blocks, options={"n": 1}, output_blocks=output_blocks
                    )
                )
            )

        # After this, because we were streaming, the block actually exists on the file.
        file.refresh()
        assert len(file.blocks) == 2

        # But the status of that second block should be failed
        assert file.blocks[1].stream_state == "aborted"

        with pytest.raises(SteamshipError):
            raw_text = file.blocks[1].raw()

def run_test_streaming(
    client: Steamship, plugin: LiteLLMPlugin, blocks: [Block], options: dict
) -> ([UsageReport], [Block]):
    blocks_to_allocate = plugin.determine_output_block_types(
        PluginRequest(data=RawBlockAndTagPluginInput(blocks=blocks, options=options))
    )
    file = File.create(client, blocks=[])
    output_blocks = []
    for block_type_to_allocate in blocks_to_allocate.data.block_types_to_create:
        assert block_type_to_allocate == MimeTypes.TXT.value
        output_blocks.append(
            Block.create(
                client,
                file_id=file.id,
                mime_type=MimeTypes.TXT.value,
                streaming=True,
            )
        )

    response = plugin.run(
        PluginRequest(
            data=RawBlockAndTagPluginInputWithPreallocatedBlocks(
                blocks=blocks, options=options, output_blocks=output_blocks
            )
        )
    )
    result_blocks = [Block.get(client, _id=block.id) for block in output_blocks]
    return response.data.usage, result_blocks


def test_multimodal_functions_with_blocks(envreset):
    with Steamship.temporary_workspace() as steamship:
        litellm = LiteLLMPlugin(client=steamship, config={})
        blocks = [
            Block(
                text="You are a helpful AI assistant.",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text="Generate an image of a sailboat.",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text=json.dumps(
                    {"name": "generate_image", "arguments": '{ "text": "sailboat" }'}
                ),
                tags=[
                    Tag(kind=TagKind.ROLE, name=RoleTag.ASSISTANT),
                    Tag(kind="function-selection", name="generate_image"),
                ],
                mime_type=MimeTypes.PNG,
            ),
            Block(
                text="c2f6818c-233d-4426-9dc5-f3c28fa33068",
                tags=[
                    Tag(
                        kind=TagKind.ROLE,
                        name="function",
                        value={TagValueKey.STRING_VALUE: "generate_image"},
                    )
                ],
                mime_type=MimeTypes.PNG,
            ),
            Block(
                text="Make the background of the image blue.",
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]

        _, new_blocks = run_test_streaming(
            steamship,
            litellm,
            blocks=blocks,
            options={
                "functions": [
                    {
                        "name": "PixToPixTool",
                        "description": "Modifies an existing image according to a text prompt.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "text prompt for a tool.",
                                },
                                "uuid": {
                                    "type": "string",
                                    "description": 'UUID for a Steamship Block. Used to refer to a non-textual input generated by another function. Example: "c2f6818c-233d-4426-9dc5-f3c28fa33068"',
                                },
                            },
                        },
                    },
                    {
                        "name": "DalleTool",
                        "description": "Generates a new image from a text prompt.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "text prompt for a tool.",
                                },
                                "uuid": {
                                    "type": "string",
                                    "description": 'UUID for a Steamship Block. Used to refer to a non-textual input generated by another function. Example: "c2f6818c-233d-4426-9dc5-f3c28fa33068"',
                                },
                            },
                        },
                    },
                ]
            },
        )
        assert len(new_blocks) == 1
        assert "function_call" in new_blocks[0].text.strip()
        function_call = json.loads(new_blocks[0].text.strip())
        assert "function_call" in function_call
        fc = function_call.get("function_call")
        assert "PixToPixTool" == fc.get("name", "")
        args = fc.get("arguments", None)
        assert args is not None
        assert "uuid" in args
        assert "c2f6818c-233d-4426-9dc5-f3c28fa33068" in args
        assert "text" in args
        assert "blue" in args


def fetch_result_text(block: Block) -> str:
    bytes = block.raw()
    return str(bytes, encoding="utf-8")


def test_prepare_messages(envreset):
    litellm = LiteLLMPlugin(
        config={},
    )

    blocks = [
        Block(
            text="You are a helpful AI assistant.\n\nNOTE: Some functions return images, video, and audio files. These multimedia files will be represented in messages as\nUUIDs for Steamship Blocks. When responding directly to a user, you SHOULD print the Steamship Blocks for the images,\nvideo, or audio as follows: `Block(UUID for the block)`.\n\nExample response for a request that generated an image:\nHere is the image you requested: Block(288A2CA1-4753-4298-9716-53C1E42B726B).\n\nOnly use the functions you have been provided with.\n",
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "system"})],
        ),
        Block(
            text="Who is the current president of Taiwan?",
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "user"})],
        ),
        Block(
            text=json.dumps({"name": "SearchTool", "arguments": "{\"text\": \"current president of Taiwan\"}"}),
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "assistant"}),
                  Tag(kind="function-selection", name="SearchTool")],
        ),
        Block(
            text="Tsai Ing-wen",
            tags=[Tag(kind=ChatTag.ROLE, name=RoleTag.FUNCTION, value={TagValueKey.STRING_VALUE: "SearchTool"})],
        ),
        Block(
            text="The current president of Taiwan is Tsai Ing-wen.",
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "assistant"})],
        ),
        Block(
            text="totally. thanks.",
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "user"})],
        ),
        Block(
            text="will be filtered out",
            tags=[Tag(kind=TagKind.CHAT, name=ChatTag.ROLE, value={TagValueKey.STRING_VALUE: "agent"})],
        )
    ]

    messages = litellm.prepare_messages(blocks=blocks)

    expected_messages = [
        {'role': 'system', 'content': 'You are a helpful AI assistant.\n\nNOTE: Some functions return images, video, and audio files. These multimedia files will be represented in messages as\nUUIDs for Steamship Blocks. When responding directly to a user, you SHOULD print the Steamship Blocks for the images,\nvideo, or audio as follows: `Block(UUID for the block)`.\n\nExample response for a request that generated an image:\nHere is the image you requested: Block(288A2CA1-4753-4298-9716-53C1E42B726B).\n\nOnly use the functions you have been provided with.\n'},
        {'role': 'user', 'content': 'Who is the current president of Taiwan?'},
        {'role': 'assistant', 'content': None, 'function_call': {'arguments': '{"text": "current president of Taiwan"}', 'name': 'SearchTool'}},
        {'role': 'function', 'content': 'Tsai Ing-wen', 'name': 'SearchTool'},
        {'role': 'assistant', 'content': 'The current president of Taiwan is Tsai Ing-wen.'},
        {'role': 'user', 'content': 'totally. thanks.'}
    ]

    for msg in messages:
        assert msg in expected_messages, f"could not find expected message: {msg}"


def test_invalid_env(envreset):
    with Steamship.temporary_workspace() as client:
        with pytest.raises(SteamshipError) as e:
            LiteLLMPlugin(
                client=client, config={"litellm_env": "BAD_ENV:abfcd;OPENAI_API_KEY:abcdefghji"},
            )
        assert "litellm environment keys must end with _API_KEY, _API_BASE, or _API_VERSION" in str(e)
        with pytest.raises(openai.AuthenticationError):
            # attempt to use openai without an openai key
            litellm = LiteLLMPlugin(
                client=client, config={"litellm_env": "REPLICATE_API_KEY:some_key"}
            )

            blocks = [
                Block(
                    text=COUNT_SYSTEM_PROMPT,
                    tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                    mime_type=MimeTypes.TXT,
                ),
                Block(
                    text=COUNT_USER_PROMPT,
                    tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                    mime_type=MimeTypes.TXT,
                ),
            ]

            run_test_streaming(client, litellm, blocks, options={})


def test_own_billing(envreset):
    with Steamship.temporary_workspace() as client:
        # Steal API key and pretend we're providing our own, but not replicate
        local_secrets = str(pathlib.Path(inspect.getfile(test_own_billing)).parent.parent / "src" / ".steamship" / "secrets.toml")
        secret_kwargs = toml.load(local_secrets)
        secret_envs = LiteLLMPlugin.get_envs(secret_kwargs["litellm_env"])
        test_env = {k: v for k, v in secret_envs.items() if k != "REPLICATE_API_KEY"}
        test_env_str = ';'.join([f"{k}:{v}" for k, v in test_env.items()])

        plugin = LiteLLMPlugin(
            client=client, config={"litellm_env": test_env_str}
        )
        blocks = [
            Block(
                text=COUNT_SYSTEM_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.SYSTEM)],
                mime_type=MimeTypes.TXT,
            ),
            Block(
                text=COUNT_USER_PROMPT,
                tags=[Tag(kind=TagKind.ROLE, name=RoleTag.USER)],
                mime_type=MimeTypes.TXT,
            ),
        ]
        # Successfully use one provided API key, without usage billed.
        usage, blocks = run_test_streaming(client, plugin, blocks, options={})
        assert not usage

        # Don't fall back to our billing if they didn't provide a key for another provider.
        with pytest.raises(AuthenticationError):
            run_test_streaming(client, plugin, blocks, options={"model": LLAMA})

        # Don't allow mucking with the options to fall back to our key, envs are set only at config time.
        with pytest.raises(SteamshipError) as e:
            run_test_streaming(client, plugin, blocks, options={"litellm_env": ""})

        assert "Configured environment (litellm_env) may not be overridden in options" in str(e)
