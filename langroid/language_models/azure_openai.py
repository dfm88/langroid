import logging
from typing import Callable

from dotenv import load_dotenv
from httpx import Timeout
from openai import AsyncAzureOpenAI, AzureOpenAI

from langroid.language_models.openai_gpt import (
    OpenAIGPT,
    OpenAIGPTConfig,
)

azureStructuredOutputList = [
    "2024-08-06",
    "2024-11-20",
]

azureStructuredOutputAPIMin = "2024-08-01-preview"

logger = logging.getLogger(__name__)


class AzureConfig(OpenAIGPTConfig):
    """
    Configuration for Azure OpenAI GPT.

    Attributes:
        type (str): should be ``azure.``
        api_version (str): can be set in the ``.env`` file as
            ``AZURE_OPENAI_API_VERSION.``
        deployment_name (str): can be set in the ``.env`` file as
            ``AZURE_OPENAI_DEPLOYMENT_NAME`` and should be based the custom name you
            chose for your deployment when you deployed a model.
        model_name (str): can be set in the ``.env``
            file as ``AZURE_OPENAI_MODEL_NAME``
            and should be based on the model name chosen during setup.
        model_version (str): can be set in the ``.env`` file as
            ``AZURE_OPENAI_MODEL_VERSION`` and should be based on the model name
            chosen during setup.
    """

    api_key: str = ""  # CAUTION: set this ONLY via env var AZURE_OPENAI_API_KEY
    type: str = "azure"
    api_version: str = "2023-05-15"
    deployment_name: str = ""
    model_name: str = ""
    model_version: str = ""  # is used to determine the cost of using the model
    api_base: str = ""

    # Alternatively, bring your own clients:
    azure_openai_client_provider: Callable[[], AzureOpenAI] | None = None
    azure_openai_async_client_provider: Callable[[], AsyncAzureOpenAI] | None = None

    # all of the vars above can be set via env vars,
    # by upper-casing the name and prefixing with `env_prefix`, e.g.
    # AZURE_OPENAI_API_VERSION=2023-05-15
    # This is either done in the .env file, or via an explicit
    # `export AZURE_OPENAI_API_VERSION=...`
    class Config:
        env_prefix = "AZURE_OPENAI_"


class AzureGPT(OpenAIGPT):
    """
    Class to access OpenAI LLMs via Azure. These env variables can be obtained from the
    file `.azure_env`. Azure OpenAI doesn't support ``completion``
    Attributes:
        config (AzureConfig): AzureConfig object
        api_key (str): Azure API key
        api_base (str): Azure API base url
        api_version (str): Azure API version
        model_name (str): the name of gpt model in your deployment
        model_version (str): the version of gpt model in your deployment
    """

    def __init__(self, config: AzureConfig):
        # This will auto-populate config values from .env file
        load_dotenv()
        super().__init__(config)
        self.config: AzureConfig = config
        if self.config.deployment_name == "":
            raise ValueError(
                """
                AZURE_OPENAI_DEPLOYMENT_NAME not set in .env file,
                please set it to your Azure openai deployment name."""
            )
        self.deployment_name = self.config.deployment_name

        if self.config.model_name == "":
            raise ValueError(
                """
                AZURE_OPENAI_MODEL_NAME not set in .env file,
                please set it to chat model name in your deployment."""
            )

        if (
            self.config.azure_openai_client_provider
            or self.config.azure_openai_async_client_provider
        ):
            if not self.config.azure_openai_client_provider:
                self.client = None
                logger.warning(
                    "Using user-provided Azure OpenAI client, but only async "
                    "client has been provided. Synchronous calls will fail."
                )
            if not self.config.azure_openai_async_client_provider:
                self.async_client = None
                logger.warning(
                    "Using user-provided Azure OpenAI client, but no async "
                    "client has been provided. Asynchronous calls will fail."
                )

            if self.config.azure_openai_client_provider:
                self.client = self.config.azure_openai_client_provider()
            if self.config.azure_openai_async_client_provider:
                self.async_client = self.config.azure_openai_async_client_provider()
                self.async_client.timeout = Timeout(self.config.timeout)
        else:
            if self.config.api_key == "":
                raise ValueError(
                    """
                    AZURE_OPENAI_API_KEY not set in .env file,
                    please set it to your Azure API key."""
                )

            if self.config.api_base == "":
                raise ValueError(
                    """
                    AZURE_OPENAI_API_BASE not set in .env file,
                    please set it to your Azure API key."""
                )

            self.client = AzureOpenAI(
                api_key=self.config.api_key,
                azure_endpoint=self.config.api_base,
                api_version=self.config.api_version,
                azure_deployment=self.config.deployment_name,
            )
            self.async_client = AsyncAzureOpenAI(
                api_key=self.config.api_key,
                azure_endpoint=self.config.api_base,
                api_version=self.config.api_version,
                azure_deployment=self.config.deployment_name,
                timeout=Timeout(self.config.timeout),
            )

        # set the chat model to be the same as the model_name
        self.config.chat_model = self.config.model_name

        self.supports_json_schema = (
            self.config.api_version >= azureStructuredOutputAPIMin
            and self.config.model_version in azureStructuredOutputList
        )
