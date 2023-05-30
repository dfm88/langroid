from llmagent.agent.chat_agent import ChatAgent
from pydantic import BaseModel, HttpUrl
from typing import Optional
from examples.codechat.code_chat_agent import CodeChatAgentConfig, CodeChatAgent
from examples.dockerchat.dockerchat_agent_messages import (
    RunPython,
    AskURLMessage,
    FileExistsMessage,
    PythonVersionMessage,
    PythonDependencyMessage,
    ValidateDockerfileMessage,
    EntryPointAndCMDMessage,
)
from rich.console import Console
from rich.prompt import Prompt
from llmagent.parsing.repo_loader import RepoLoader, RepoLoaderConfig
from examples.dockerchat.identify_python_version import get_python_version
from examples.dockerchat.identify_python_dependency import (
    identify_dependency_management,
)

import os
import logging
import docker
import time
import datetime

console = Console()
logger = logging.getLogger(__name__)
DEFAULT_URL = "https://github.com/eugeneyan/testing-ml"
# Message types that can be handled by the agent;
# each corresponds to a method in the agent.

NO_ANSWER = "I don't know"

DOCKER_CODE_CHAT_INSTRUCTIONS = """
You are a savvy python programmer who has access to a python repository.
Your would like to make a dockerfile for this repo, but you are NOT a 
docker expert. I am a docker expert, and I know how to make a dockerfile
in general, but since I do not have access to the repo, I will rely on 
you for information about the codebase. Your task is to answer my questions about 
the codebase. You will be given various extracts from the codebase, 
such as directory listings or file contents. You can use this information to answer
my questions. When answering my questions, keep in mind that my goal is to build a
Dockerfile for the codebase. For example, if I ask you if a certain file
exists, and it does not occur in the listings you are shown, then you can
simply answer "No". 
"""


class UrlModel(BaseModel):
    url: HttpUrl


class DockerChatAgent(ChatAgent):
    url: str = "https://github.com/eugeneyan/testing-ml"
    repo_tree: str = None
    repo_path: str = None
    code_chat_agent: CodeChatAgent = None

    def process_pending_message(self) -> None:
        super().process_pending_message()
        if self.current_response is None:
            result = self.code_chat_agent.do_task(
                msg=self.pending_message.content, main=False
            )
            self.current_response = result
            if result is not None:
                self.pending_message = result
                # from now on, continue as if the USER sent this msg!
                self.setup_task(msg=result.content)

    def handle_message_fallback(self, input_str: str = "") -> Optional[str]:
        if self.repo_path is None and "URL" not in input_str:
            return """
            You have not sent me the URL for the repo yet. 
            Please ask me for the URL, and once you receive it, 
            send it to me for confirmation. Once I confirm the URL, 
            you can proceed.
            """

    def run_python(self, msg: RunPython) -> str:
        # TODO: to be implemented. Return dummy msg for now
        logger.error(
            f"""
        This is a placeholder for the run_python method.
        Here is the code:
        {msg.code}
        """
        )
        return "No results, please continue asking your questions."

    def ask_url(self, msg: AskURLMessage) -> str:
        while True:
            url = Prompt.ask(
                "[blue]Please enter the URL of the repo, or hit enter to use default"
            )
            if url == "":
                url = DEFAULT_URL
            try:
                url_model = UrlModel(url=url)
            except ValueError as e:
                Prompt.ask(f"[blue]A valid URL was not seen: {e}; Please try again")
            if url_model.url is not None:
                break

        self.url = url_model.url
        code_chat_cfg = CodeChatAgentConfig(
            name="Coder",
            repo_url=self.url,
            instructions=DOCKER_CODE_CHAT_INSTRUCTIONS,
            content_includes=["txt", "md", "yml", "yaml", "sh", "Makefile"],
            content_excludes=["Dockerfile"],
            # USE same LLM settings as DockerChatAgent, e.g.
            # if DockerChatAgent uses gpt4, then use gpt4 here too
            llm=self.config.llm,
        )
        # Note `content_includes` and `content_excludes` are used in
        # self.code_chat_agent to create a json dump of (top k lines) of various
        # files, to be included in the initial LLM message.
        self.code_chat_agent = CodeChatAgent(code_chat_cfg)
        self.repo_loader = RepoLoader(self.url, RepoLoaderConfig())
        self.repo_path = self.repo_loader.clone()
        # get the repo tree to depth d, with first k lines of each file
        self.repo_tree, _ = self.repo_loader.load(depth=1, lines=20)
        repo_listing = "\n".join(self.repo_loader.ls(self.repo_tree, depth=1))

        return f"""
        Based on the URL, here is some information about the repo that you can use.  
        
        First, here is a list of ALL the files and directories at the ROOT of the 
        repo. Any files of interest to you MUST be in this list, there you do NOT 
        need to ask in future about whether any file exists.
        {repo_listing}
        In later parts of the conversation, only ask questions that CANNOT 
        be answered by the information above. Do not ask for any info that is already 
        provided above! 
        """

    def python_version(self, m: PythonVersionMessage) -> str:
        """
        Identifies Python version for a given repo
        Args:
        Returns:
            str: a string indicates the identified python version or indicate the
                version can't be identified
        """
        if self.repo_path is None:
            return self.handle_message_fallback()
        answer = self.ask_agent(
            self.code_chat_agent,
            request="What is the Python version of this repo?",
            no_answer=NO_ANSWER,
            user_confirm=False,
        )
        if answer is not None:
            return answer

        answer = get_python_version(self.repo_path)
        if answer:
            return answer
        else:
            logger.error("Could not determine Python version.")
        return "Couldn't identify the python version"

    def file_exists(self, message: FileExistsMessage) -> str:
        if self.repo_path is None:
            return self.handle_message_fallback()
        # dummy result, fill with actual code.

        answer = self.ask_agent(
            self.code_chat_agent,
            request=f"Does this project contain a file named {message.filename}?",
            no_answer=NO_ANSWER,
            user_confirm=False,
        )
        if answer is not None:
            return answer

        matches = RepoLoader.select(self.repo_tree, includes=[message.filename])
        exists = False
        if len(matches) > 0:
            exists = len(matches["files"]) > 0

        if exists:
            return f"""
            Yes, there is a file named {message.filename} in the repo."""
        else:
            return f"""
            No, there is no file named {message.filename} in the repo."""

    def python_dependency(self, m: PythonDependencyMessage) -> str:
        """
        Identifies Python dependencies in a given repo by inspecting various
            artifacts like requirements.txt
        Args:
        Returns:
            str: a string indicates the identified the dependency management approach
        """
        if self.repo_path is None:
            return self.handle_message_fallback()

        answer = self.ask_agent(
            self.code_chat_agent,
            request="Which file is used to manage dependencies in this project?",
            no_answer=NO_ANSWER,
            user_confirm=False,
        )
        if answer is not None:
            return answer

        python_dependency = identify_dependency_management(self.repo_path)
        if python_dependency:
            return f"Dependencies in this repo are managed using: {python_dependency}"
        else:
            return "Dependencies are not defined in this repo"

    def _cleanup_dockerfile(self, img_id: str, dockerfile_path: str) -> None:
        """
        Remove Dockefile and built image after performing the verification process
        Args:
            img_id (str): the ID of the Docker image
            dockerfile_path (str): path to the saved Dockerfile
        """
        client = docker.from_env()

        try:
            if os.path.exists(dockerfile_path):
                os.remove(dockerfile_path)
                logger.info(f"Dockerfile at path '{dockerfile_path}' has been removed.")
            else:
                logger.error(f"No Dockerfile found at path '{dockerfile_path}'.")
            # Remove Dockerfile_proposed
            client.images.remove(img_id)
            client.images.get(img_id)
            logger.error("Image removal failed!")
        except docker.errors.ImageNotFound:
            logger.info("Image removed successfully!")

    def _save_dockerfile(self, dockerfile: str, proposed_dockerfile_name: str) -> str:
        """
        Save the proposed Dockerfile in the root directory of a repo
        Args:
            dockerfile (str): content of the dockerfile
            proposed_dockerfile_name (str): the name of the Dockerfile,
                better to use a different name to avoid changing existing one (if any).
        Returns:
            str: a string indicates whether the Dockerfile has been saved successfully
        """
        try:
            full_path = os.path.join(self.repo_path, proposed_dockerfile_name)
            with open(full_path, "w") as f:
                f.write(dockerfile)
            return full_path
        except Exception as e:
            return f"An error occurred while saving the Dockerfile: {e}"

    def _build_docker_image(self, proposed_doeckerfile_name: str, img_tag: str):
        """
        Build docker image based on the repo_path by using docker SDK
        Args:
            proposed_doeckerfile_name (str): the name of the proposed Dockerfile
            that should be used to build the image
            img_tag (str): the name of the Docker image that will be built based on the
            proposed_doeckerfile_name
        Returns:
            A tuple comprises three items: First, object for the image that was
            built (if succeeded), otherwise, returns None. Second, message indicates
            whetehr the build process succeeded or failed.
            Third, build time or None (if failed)
        """
        try:
            start = time.time()
            # I noticed the flag ``rm`` isn't used anymore,
            # so I need to do the cleanup myself later on
            with console.status("Verifying the proposed Dockerfile..."):
                image, build_logs = docker.from_env().images.build(
                    rm=True,
                    path=self.repo_path,
                    tag=img_tag,
                    dockerfile=proposed_doeckerfile_name,
                )
            build_time = time.time() - start
            formatted_build_time = "{:.2f}".format(
                datetime.timedelta(seconds=build_time).total_seconds()
            )
        except docker.errors.DockerException as e:
            return (None, f"Image build failed: {e}", None)

        return (image, "Image build successful!", formatted_build_time)

    def validate_dockerfile(
        self,
        dockerfile_msg: ValidateDockerfileMessage,
        confirm: bool = True,
    ) -> str:
        """
        validates the proposed Dockerfile by LLM. The validation process involves
        saving the proposed_dockerfile, building the image, and finally cleanning up
        Args:
            dockerfile_msg (ValidateDockerfileMessage): LLM message contains the
            definition of the Dockerfile
        Returns:
            str: a string indicates whether the Dockerfile has been built successfully
        """
        if self.repo_path is None:
            return self.handle_message_fallback()

        if type(dockerfile_msg.proposed_dockerfile) != str:
            dockerfile_msg.proposed_dockerfile = "\n".join(
                dockerfile_msg.proposed_dockerfile
            )
        if len(dockerfile_msg.proposed_dockerfile) < 20:
            return """
            The `proposed_dockerfile` parameter is invalid;
            Note this parameter should contain the CONTENTS of the 
            proposed dockerfile, NOT the NAME of the Dockerfile
            """

        if confirm:
            user_response = Prompt.ask(
                "Please confirm dockerfile validation",
                choices=["y", "n"],
                default="y",
            )
            if user_response.lower() != "y":
                return """"
                    Not ready for dockerfile validation, please 
                    continue with your next question or request for information.
                    """

        proposed_dockerfile_content = dockerfile_msg.proposed_dockerfile
        # It's better to have a different name other than the default name,
        # good for comparison in the future between
        # generated Dockerfile and existing Dockerfile
        proposed_dockerfile_name = "Dockerfile_proposed"
        img_tag = "validate_img"
        dockerfile_path = self._save_dockerfile(
            proposed_dockerfile_content, proposed_dockerfile_name
        )
        if dockerfile_path.startswith("An error"):
            return dockerfile_path

        img, build_log, build_time = self._build_docker_image(
            proposed_dockerfile_name, img_tag
        )

        if img:
            self._cleanup_dockerfile(img.id, dockerfile_path)
            return f"Docker image built successfully and build time took:{build_time} Seconds..."
        else:
            return f"Docker build failed with error message: {build_log}"

    def find_entrypoint(self, m: EntryPointAndCMDMessage) -> str:
        """
        Finds corresponding command to the ENTRYPOINT
        Args:
            m (EntryPointAndCMDMessage): LLM message contains a request to identify
                entrypoints
        Retruns:
            str: description of the main scripts and corresponding argument in the
                repo that are potential candidates to become ENTRYPOINT
        """
        if self.repo_path is None:
            return self.handle_message_fallback()

        answer = self.ask_agent(
            self.code_chat_agent,
            request="""What's the name of main script in this repo and can you SPECIFY 
            the command line and necessary arguments to run the main script? 
            If there are more than one main script, then SPECIFY the commands 
            and necessary arguments corresponding to each one
            """,
            no_answer=NO_ANSWER,
            user_confirm=False,
        )
        if answer is not None:
            return answer

        return "I couldn't identify potentail main scripts for the ENTRYPOINT"