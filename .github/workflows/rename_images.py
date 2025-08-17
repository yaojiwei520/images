import os
import git
import requests
import time
import zipfile
import io
import json
from github import Github
import urllib.parse
import openai
from PIL import Image  # 导入 Pillow 库用于获取图像尺寸
from typing import Optional

def describe_and_rename_image(zip_file_url: str, github_url: str, original_image_path: str, repo: git.Repo, github_token: str, release_name: str):
    """
    下载 ZIP 文件，解压 markdown 文件，使用大模型总结内容，并重命名原始图像文件。
    """
    try:
        print(f"下载 zip 文件: {zip_file_url}")
        zip_response = requests.get(zip_file_url)
        zip_response.raise_for_status()

        markdown_content = extract_markdown_and_upload_to_release(zip_response.content, github_token, repo.working_dir, release_name)
        if not markdown_content:
            print("ZIP 文件中没有找到 markdown 文件或提取失败")
            return False

        summary = summarize_text_with_openai(markdown_content)
        if not summary:
            print("无法总结 Markdown 文件内容")
            return False

        local_path = get_local_path_from_github_url(github_url)
        if not local_path:
            print("无法解析 GitHub URL")
            return False

        filename, ext = os.path.splitext(os.path.basename(local_path))
        new_filename = sanitize_filename(summary) + ext
        new_path = os.path.join(os.path.dirname(local_path), new_filename)


        repo.git.mv(local_path, new_path)
        print(f"重命名 {local_path} -> {new_path}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"下载 ZIP 文件失败：{e}")
        return False
    except Exception as e:
        print(f"处理 ZIP 文件或重命名文件出错: {e}")
        return False


def extract_markdown_and_upload_to_release(zip_content: bytes, github_token: str, repo_dir: str, release_name: str) -> Optional[str]:
    """
    从 ZIP 文件中提取 Markdown 内容，上传 ZIP 到 GitHub Release。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            markdown_content = None
            for filename in z.namelist():
                if filename.endswith(".md"):
                    with z.open(filename) as f:
                        markdown_content = f.read().decode("utf-8")
                        break
            upload_zip_to_release(github_token, repo_dir, zip_content, release_name)
            return markdown_content
    except Exception as e:
        print(f"处理 ZIP 文件出错: {e}")
        return None


def upload_zip_to_release(github_token: str, repo_dir: str, zip_content: bytes, release_name: str):
    """
    上传 ZIP 文件到 GitHub Release。
    """
    try:
        g = Github(github_token)
        repo = g.get_repo(os.environ["GITHUB_REPOSITORY"])
        release = None

        for r in repo.get_releases():
            if r.title == release_name:
                release = r
                break

        if release is None:
            release = repo.create_git_release(tag=release_name, name=release_name, message="Release " + release_name, draft=False, prerelease=False)

        release.upload_asset(data=io.BytesIO(zip_content), name="mineru_output.zip", content_type="application/zip")
        print(f"成功上传 mineru_output.zip 到 Release {release_name}")

    except Exception as e:
        print(f"上传 ZIP 文件到 Release 出错: {e}")


def summarize_text_with_openai(text: str) -> Optional[str]:
    """使用 OpenAI API 总结文本内容。"""
    try:
        openai_api_key = os.environ.get("OPENAI_API_KEY", "dummy_key") # 使用默认值防止报错,不验证key
        openai_api_base = os.environ.get("OPENAI_API_BASE", "https://free.v36.cm") # 使用默认值

        if not openai_api_key:
            print("请设置 OPENAI_API_KEY 环境变量!")
            #return None # 为了方便测试, 使用默认key和base, 不强制退出

        client = openai.OpenAI(
            api_key=openai_api_key,
            base_url=openai_api_base  # 使用环境变量中的 base_url
        )

        prompt = (
            "请以简洁准确的方式总结以下内容，总结限制在15个字内，不需要添加任何内容中没有提及的信息，"
            "如果内容中没有提到某个事物，请不要虚构或猜测它是否存在: \n{text}"
        ).format(text=text)

        response = client.chat.completions.create(
            messages=[
                {'role': 'user', 'content': prompt},  # 使用 prompt
            ],
            model='gpt-4o-mini',  # 模型改为 gpt-4o-mini
            max_tokens=50,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip()  # 获取 content
        return summary

    except openai.error.OpenAIError as e:
        print(f"调用 OpenAI API 出错: {e}")
        return None
    except Exception as e:
        print(f"总结文本出错: {e}")
        return None


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除特殊字符和空格。
    """
    return "".join(c if c.isalnum() else "_" for c in filename)

def get_local_path_from_github_url(github_url: str) -> Optional[str]:
    """将 GitHub 网页 URL 转换为本地仓库中的文件路径"""
    try:
        parts = github_url.replace("https://github.com/", "").split("/")
        username = parts[0]
        repo_name = parts[1]
        branch = parts[3]
        file_path = "/".join(parts[4:])
        return file_path
    except Exception as e:
        print(f"解析 GitHub URL 失败: {e}")
        return None



if __name__ == "__main__":
    # 获取环境变量
    repo = git.Repo("./", search_parent_directories=True)
    github_token = os.environ.get("GITHUB_TOKEN")
    release_name = os.environ.get("GITHUB_REF_NAME")
    github_repository = os.environ.get("GITHUB_REPOSITORY")

    if not all([github_token, release_name, github_repository]):
        print("请确保已设置 GITHUB_TOKEN, GITHUB_REF_NAME 和 GITHUB_REPOSITORY 环境变量")
        exit(1)

    # 从环境变量中获取触发事件的文件路径
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        with open(event_path, "r") as f:
            event_data = json.load(f)
            # 尝试获取所有已修改的文件
            files = []
            if "commits" in event_data:  # push 事件
                for commit in event_data["commits"]:
                    files.extend([file["filename"] for file in commit.get("added", []) + commit.get("modified", []) if file["filename"].startswith("images/")])
            elif "pull_request" in event_data:  # pull_request 事件,使用不同的方式获取文件
                print(" pull_request  事件,使用不同的方式获取文件")
                 # 获取 PR 修改的文件列表
                pull_request = event_data["pull_request"]
                pr_number = pull_request["number"]
                g = Github(github_token)
                repo = g.get_repo(github_repository)
                pr = repo.get_pull(pr_number)
                files = [file.filename for file in pr.get_files()  if file.filename.startswith("images/")] # 只获取 images 目录下的文件

            else:
                print("不支持的事件类型")
                exit(1)

            # 过滤掉非图片文件
            image_files = [file for file in files if file.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp",".webp"))]

            if not image_files:
                print("没有找到需要处理的图片文件")
                exit(0)

            print(f"需要处理的图片文件: {image_files}")

            for image_file in image_files:
                # 1.  构造 Mineru API 参数
                file_name = os.path.basename(image_file)
                github_url = f"https://github.com/{github_repository}/blob/{release_name}/{image_file}"  # 构造 github_url

                # 获取原始图像的本地路径
                original_image_path = image_file

                # 构建 github raw url 方便调用mineru API
                username = github_repository.split("/")[0]
                repo_name = github_repository.split("/")[1]
                raw_url = f"https://raw.githubusercontent.com/{username}/{repo_name}/{release_name}/{image_file}"
                raw_url_encoded = urllib.parse.quote(raw_url, safe='/:')
                print(f"Encoded Raw URL: {raw_url_encoded}")

                # 2.  调用 Miner API  创建任务
                mineru_api_endpoint = os.environ.get("MINERU_API_ENDPOINT")
                mineru_token = os.environ.get("MINERU_API_TOKEN")
                url = f'{mineru_api_endpoint}/api/v4/extract/task'
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {mineru_token}'
                }
                data = {
                    'url': raw_url_encoded,
                    'is_ocr': True,
                    'enable_formula': False,
                    'enable_table': True
                }

                try:
                    res = requests.post(url, headers=headers, json=data)
                    res.raise_for_status()

                    task_id = res.json().get("data", {}).get("task_id")
                    task_url = f'{mineru_api_endpoint}/api/v4/extract/task/{task_id}'
                except requests.exceptions.RequestException as e:
                    print(f"调用 Mineru API 失败: {e}")
                    continue  # 继续处理下一个文件
                except json.JSONDecodeError as e:
                    print(f"解析JSON响应失败：{e}")
                    continue  # 继续处理下一个文件
                except Exception as e:
                    print(f"发生错误: {e}")
                    continue  # 继续处理下一个文件

                # 3. 轮询任务状态，直到完成
                max_retries = 20
                retry_delay = 5
                full_zip_url = None
                for attempt in range(max_retries):
                    time.sleep(retry_delay)
                    try:
                        task_res = requests.get(task_url, headers=headers)
                        task_res.raise_for_status()
                        task_data = task_res.json().get("data", {})
                        state = task_data.get("state")

                        if state == "done":
                            full_zip_url = task_data.get("full_zip_url")
                            break
                        elif state == "failed":
                            print(f"Mineru API 任务失败: {task_data.get('err_msg')}")
                            full_zip_url =None
                            break  # 跳出轮询，并处理下一个文件
                        else:
                            print(f"任务仍在处理中... (状态: {state}, 尝试次数: {attempt + 1}/{max_retries})")
                    except requests.exceptions.RequestException as e:
                        print(f"查询任务状态失败: {e}")
                        break  # 跳出轮询，并处理下一个文件
                    except json.JSONDecodeError as e:
                        print(f"解析JSON响应失败：{e}")
                        break  # 跳出轮询，并处理下一个文件
                    except Exception as e:
                        print(f"发生错误: {e}")
                        break  # 跳出轮询，并处理下一个文件

                if not full_zip_url:
                    print("无法获取 full_zip_url, 跳过该文件")
                    continue #  处理下一个文件
                # 4. 描述图片和重命名图片
                if describe_and_rename_image(full_zip_url, github_url,original_image_path, repo, github_token, release_name):
                    repo.git.add(all=True)
                    try:
                        repo.git.commit('-m', f'重命名图片 (AI): {file_name}')
                        repo.git.push()
                    except Exception as e:
                        print(f"git commit 或 push 失败: {e}")
                        exit(1)

                else:
                    print("重命名图片失败")
                    exit(1)
    else:
        print("未找到 event payload 文件")
        exit(1)
