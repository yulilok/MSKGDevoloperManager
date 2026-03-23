import discord
from discord import app_commands, Embed, SelectOption
from discord.ui import Button, View, Modal, TextInput, Select
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import config
import uuid
from datetime import datetime, timedelta

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(config.CREDENTIALS_PATH, scope)
client = gspread.authorize(creds)

# Try to open by configured sheet ID
sheet = client.open_by_key(config.SHEET_ID).sheet1

# Initialize sheet headers if needed
EXPECTED_HEADERS = ['ID', 'Name', 'Description', 'Status', 'Priority', 'Deadline', 'Assignee', 
                    'SubmittedBy', 'Role', 'RejectionReason', 'CompletionLink', 'CompletionFile', 
                    'CompletedAt', 'Reviewer']

def init_sheet_headers():
    """Initialize sheet headers if they don't exist"""
    try:
        current_headers = sheet.row_values(1)
        if not current_headers or current_headers != EXPECTED_HEADERS:
            sheet.update('A1:N1', [EXPECTED_HEADERS])
            print("Google Sheets headers initialized")
    except Exception as e:
        print(f"Error initializing sheet headers: {e}")

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Store pending task completions: {message_id: {'task_id': ..., 'task_name': ..., 'user_id': ...}}
# Stored in memory only - temporary data that resets on bot restart
pending_completions = {}

# Helper functions
def has_role(member, role_id):
    return any(role.id == role_id for role in member.roles)

def get_tasks():
    records = sheet.get_all_records()
    return records

def add_task(name, description, submitted_by, submitter_id):
    task_id = str(uuid.uuid4())[:8]
    # Columns: ID, Name, Description, Status, Priority, Deadline, Assignee, SubmittedBy, Role, RejectionReason, CompletionLink, CompletionFile, CompletedAt, Reviewer
    sheet.append_row([task_id, name, description, 'pending_approval', '', '', '', submitted_by, '', '', '', '', '', ''])
    return task_id

def update_task(task_id, updates):
    records = sheet.get_all_records()
    for i, record in enumerate(records):
        if record['ID'] == task_id:
            for key, value in updates.items():
                sheet.update_cell(i+2, list(record.keys()).index(key)+1, value)
            break

def get_task_row_number(task_id):
    records = sheet.get_all_records()
    for i, record in enumerate(records):
        if record['ID'] == task_id:
            return i + 2  # +2 because row 1 is header and records are 0-indexed
    return None

def add_completion_record(task_id, user_id, user_name, link=None, filename=None):
    """Add completion record to the sheet"""
    row = get_task_row_number(task_id)
    if row:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if link:
            sheet.update_cell(row, list(sheet.row_values(1)).index('CompletionLink') + 1, link)
        if filename:
            sheet.update_cell(row, list(sheet.row_values(1)).index('CompletionFile') + 1, filename)
        sheet.update_cell(row, list(sheet.row_values(1)).index('CompletedAt') + 1, timestamp)

def set_task_reviewer(task_id, reviewer_id):
    """Set the reviewer for a completed task"""
    row = get_task_row_number(task_id)
    if row:
        sheet.update_cell(row, list(sheet.row_values(1)).index('Reviewer') + 1, reviewer_id)


class TaskCompletionModal(Modal, title="Завершение задачи"):
    """Modal for submitting task completion with link"""
    link = TextInput(
        label="Ссылка на результат (необязательно)",
        placeholder="https://github.com/... или другая ссылка",
        required=False,
        max_length=500
    )
    comment = TextInput(
        label="Комментарий (необязательно)",
        placeholder="Опишите, что было сделано...",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, task_id, task_name, attachment=None):
        super().__init__()
        self.task_id = task_id
        self.task_name = task_name
        self.attachment = attachment

    async def on_submit(self, interaction: discord.Interaction):
        # Update task status
        update_task(self.task_id, {'Status': 'review'})

        # Save completion record to Google Sheets
        add_completion_record(
            task_id=self.task_id,
            user_id=str(interaction.user.id),
            user_name=str(interaction.user),
            link=self.link.value,
            filename=None
        )

        # Create completion embed
        embed = Embed(
            title=f"Задача завершена: {self.task_name}",
            description=self.comment.value if self.comment.value else "Без комментария",
            color=0x00ff00
        )
        embed.add_field(name="Выполнил", value=f"{interaction.user.mention}", inline=True)
        embed.add_field(name="ID задачи", value=self.task_id, inline=True)

        if self.link.value:
            embed.add_field(name="Ссылка", value=self.link.value, inline=False)

        # Send to approval channel
        leadership_channel = bot.get_channel(config.APPROVAL_CHANNEL_ID)
        if leadership_channel:
            if self.attachment:
                await leadership_channel.send(
                    f"Задача завершена {interaction.user.mention} и готова к проверке.",
                    embed=embed,
                    file=self.attachment
                )
            else:
                await leadership_channel.send(
                    f"Задача завершена {interaction.user.mention} и готова к проверке.",
                    embed=embed
                )

        try:
            await interaction.response.send_message(
                f"Задача **{self.task_name}** отправлена на проверку",
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass  # Interaction expired


class TaskCompletionLinkModal(Modal, title="Добавить ссылку"):
    """Modal for adding a link to task completion"""
    link = TextInput(
        label="Ссылка на результат",
        placeholder="https://github.com/... или другая ссылка",
        required=True,
        max_length=500
    )
    
    def __init__(self, task_id, task_name, attachment=None):
        super().__init__()
        self.task_id = task_id
        self.task_name = task_name
        self.attachment = attachment
    
    async def on_submit(self, interaction: discord.Interaction):
        # Store link and show final confirmation
        view = TaskCompletionConfirmView(self.task_id, self.task_name, self.attachment, self.link.value)
        embed = Embed(
            title=f"Завершение задачи: {self.task_name}",
            description=f"Файл: {self.attachment.filename if self.attachment else 'Нет'}\nСсылка: {self.link.value}",
            color=0x3498db
        )
        try:
            await interaction.message.edit(embed=embed, view=view)
        except discord.errors.NotFound:
            pass
        try:
            await interaction.response.send_message("Ссылка добавлена", ephemeral=True)
        except discord.errors.NotFound:
            pass  # Interaction expired


class TaskCompletionConfirmView(View):
    """View for confirming task completion with optional attachment"""
    def __init__(self, task_id, task_name, attachment=None, link=None):
        super().__init__(timeout=120)  # 2 minute timeout
        self.task_id = task_id
        self.task_name = task_name
        self.attachment = attachment
        self.link = link

        self.add_link_btn = Button(
            label="Добавить ссылку",
            style=discord.ButtonStyle.primary,
            custom_id=f"add_link_{task_id}"
        )
        self.add_link_btn.callback = self.add_link_callback
        self.add_item(self.add_link_btn)

        self.submit_btn = Button(
            label="Отправить на проверку",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_complete_{task_id}"
        )
        self.submit_btn.callback = self.submit_callback
        self.add_item(self.submit_btn)

    async def add_link_callback(self, interaction: discord.Interaction):
        modal = TaskCompletionLinkModal(self.task_id, self.task_name, self.attachment)
        await interaction.response.send_modal(modal)

    async def submit_callback(self, interaction: discord.Interaction):
        # Get attachment from the message if any
        attachment_filename = None
        if interaction.message and interaction.message.attachments:
            attachment_filename = interaction.message.attachments[0].filename
        
        # Update task status
        update_task(self.task_id, {'Status': 'review'})
        
        # Save completion record to Google Sheets
        add_completion_record(
            task_id=self.task_id,
            user_id=str(interaction.user.id),
            user_name=str(interaction.user),
            link=self.link,
            filename=attachment_filename
        )
        
        # Create completion embed
        embed = Embed(
            title=f"Задача завершена: {self.task_name}",
            description="Задача отправлена на проверку",
            color=0x00ff00
        )
        embed.add_field(name="Выполнил", value=f"{interaction.user.mention}", inline=True)
        embed.add_field(name="ID задачи", value=self.task_id, inline=True)
        
        if self.link:
            embed.add_field(name="Ссылка", value=self.link, inline=False)
        
        # Get attachment from the message if any
        attachment = None
        if interaction.message and interaction.message.attachments:
            attachment = await interaction.message.attachments[0].to_file()
        
        # Send to approval channel
        leadership_channel = bot.get_channel(config.APPROVAL_CHANNEL_ID)
        if leadership_channel:
            if attachment:
                await leadership_channel.send(
                    f"Задача завершена {interaction.user.mention} и готова к проверке.",
                    embed=embed,
                    file=attachment,
                    view=TaskReviewView(self.task_id, self.task_name, str(interaction.user.id))
                )
            else:
                await leadership_channel.send(
                    f"Задача завершена {interaction.user.mention} и готова к проверке.",
                    embed=embed,
                    view=TaskReviewView(self.task_id, self.task_name, str(interaction.user.id))
                )

        # Delete the completion message
        try:
            await interaction.message.delete()
        except discord.errors.NotFound:
            pass  # Message already deleted

        try:
            await interaction.response.send_message(
                f"Задача **{self.task_name}** отправлена на проверку",
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass  # Interaction expired

        # Clean up pending completion
        if interaction.message.id in pending_completions:
            del pending_completions[interaction.message.id]


class TaskReviewView(View):
    """View for reviewing completed tasks"""
    def __init__(self, task_id, task_name, assignee_id):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.task_name = task_name
        self.assignee_id = assignee_id

        self.accept_btn = Button(
            label="Принять",
            style=discord.ButtonStyle.green,
            custom_id=f"review_accept_{task_id}"
        )
        self.accept_btn.callback = self.accept_callback
        self.add_item(self.accept_btn)

        self.reject_btn = Button(
            label="Отклонить",
            style=discord.ButtonStyle.red,
            custom_id=f"review_reject_{task_id}"
        )
        self.reject_btn.callback = self.reject_callback
        self.add_item(self.reject_btn)

    async def accept_callback(self, interaction: discord.Interaction):
        # Update task status
        update_task(self.task_id, {'Status': 'completed'})
        set_task_reviewer(self.task_id, str(interaction.user.id))

        # Disable buttons
        for item in self.children:
            item.disabled = True

        try:
            await interaction.message.edit(view=self)
        except discord.errors.NotFound:
            pass

        # Notify developer in DM
        try:
            developer = await bot.fetch_user(int(self.assignee_id))
            embed = Embed(
                title="Задача принята",
                description=f"Ваша задача **{self.task_name}** была принята",
                color=0x00ff00
            )
            embed.add_field(name="ID задачи", value=self.task_id, inline=True)
            embed.add_field(name="Проверил", value=f"{interaction.user.mention}", inline=True)
            await developer.send(embed=embed)
        except (discord.errors.Forbidden, discord.errors.NotFound, ValueError):
            pass  # Cannot DM user

        try:
            await interaction.response.send_message(
                f"Задача **{self.task_name}** принята. Разработчик уведомлён.",
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass  # Interaction expired

    async def reject_callback(self, interaction: discord.Interaction):
        # Show rejection modal
        modal = TaskRejectionModal(self.task_id, self.task_name, self.assignee_id)
        await interaction.response.send_modal(modal)


class TaskRejectionModal(Modal, title="Отклонение задачи"):
    """Modal for rejecting a task with reason"""
    reason = TextInput(
        label="Причина отклонения",
        placeholder="Опишите, что нужно исправить...",
        required=True,
        max_length=1000,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, task_id, task_name, assignee_id):
        super().__init__()
        self.task_id = task_id
        self.task_name = task_name
        self.assignee_id = assignee_id

    async def on_submit(self, interaction: discord.Interaction):
        # Update task status
        update_task(self.task_id, {
            'Status': 'in_progress',
            'RejectionReason': self.reason.value
        })

        # Disable the review buttons by creating a new empty view
        try:
            await interaction.message.edit(view=None)
        except discord.errors.NotFound:
            pass

        # Notify developer in DM
        try:
            developer = await bot.fetch_user(int(self.assignee_id))
            embed = Embed(
                title="Задача отклонена",
                description=f"Ваша задача **{self.task_name}** отклонена и требует доработки.",
                color=0xff0000
            )
            embed.add_field(name="ID задачи", value=self.task_id, inline=True)
            embed.add_field(name="Причина", value=self.reason.value, inline=False)
            embed.add_field(name="Проверил", value=f"{interaction.user.mention}", inline=True)
            await developer.send(embed=embed)
        except (discord.errors.Forbidden, discord.errors.NotFound, ValueError):
            pass  # Cannot DM user

        try:
            await interaction.response.send_message(
                f"Задача **{self.task_name}** отклонена. Разработчик уведомлён.",
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass  # Interaction expired


class TaskCompleteSelect(Select):
    """Dropdown for selecting a task to complete"""
    def __init__(self, tasks):
        options = [
            SelectOption(
                label=f"{task['Name'][:50]}{'...' if len(task['Name']) > 50 else ''}",
                description=f"Приоритет: {task['Priority']} | Дедлайн: {task['Deadline'] or 'Нет'}",
                value=task['ID']
            )
            for task in tasks
        ]
        super().__init__(
            placeholder="Выберите задачу для завершения...",
            options=options,
            custom_id="task_complete_select"
        )
        self.tasks = tasks

    async def callback(self, interaction: discord.Interaction):
        task_id = self.values[0]
        task = next((t for t in self.tasks if t['ID'] == task_id), None)

        if not task:
            try:
                await interaction.response.send_message("Задача не найдена.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        # Store pending completion with message_id as key
        pending_completions[interaction.message.id] = {
            'task_id': task_id,
            'task_name': task['Name'],
            'user_id': interaction.user.id
        }

        # Show completion confirmation view
        embed = Embed(
            title=f"Завершение задачи: {task['Name']}",
            description="Прикрепите файл к сообщению (если нужно) и нажмите **Отправить на проверку**.\n\nИли добавьте ссылку через кнопку ниже.",
            color=0x3498db
        )
        embed.add_field(name="Приоритет", value=task['Priority'], inline=True)
        embed.add_field(name="Дедлайн", value=task['Deadline'] or 'Нет', inline=True)

        view = TaskCompletionConfirmView(task_id, task['Name'])
        try:
            await interaction.response.send_message(embed=embed, view=view)
        except discord.errors.NotFound:
            pass  # Interaction expired


class TaskView(View):
    def __init__(self, tasks, user):
        super().__init__(timeout=None)
        select = TaskCompleteSelect(tasks)
        self.add_item(select)


class TaskClaimView(View):
    """View for claiming tasks in the tasks channel"""
    def __init__(self, task_id, task_name, task_description, role, priority, deadline, notes):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.task_name = task_name
        self.task_description = task_description
        self.role = role
        self.priority = priority
        self.deadline = deadline
        self.notes = notes

        claim_btn = Button(
            label="Взять задачу",
            style=discord.ButtonStyle.success,
            custom_id=f"claim_{task_id}",
        )
        claim_btn.callback = self.claim_callback
        self.add_item(claim_btn)

    async def claim_callback(self, interaction: discord.Interaction):
        tasks = get_tasks()
        task = next((t for t in tasks if t['ID'] == self.task_id), None)

        if not task:
            try:
                await interaction.response.send_message("Задача не найдена.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        if task.get('Assignee'):
            try:
                await interaction.response.send_message(f"Задача уже взята другим разработчиком.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        if task.get('Status') not in ['approved', 'in_progress']:
            try:
                await interaction.response.send_message("Задача недоступна для выполнения.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        # Claim the task
        update_task(self.task_id, {
            'Status': 'in_progress',
            'Assignee': str(interaction.user.id)
        })

        # Update the message with full task info
        embed = Embed(
            title=f"{self.task_name}",
            description=self.task_description,
            color=0x00ff00
        )
        embed.add_field(name="Взял задачу", value=f"{interaction.user.mention}", inline=False)
        embed.add_field(name="Роль", value=self.role, inline=True)
        embed.add_field(name="Приоритет", value=self.priority.capitalize(), inline=True)
        if self.deadline:
            embed.add_field(name="Дедлайн", value=self.deadline, inline=True)
        if self.notes:
            embed.add_field(name="Заметки", value=self.notes, inline=False)

        # Disable the claim button
        for child in self.children:
            child.disabled = True

        try:
            await interaction.message.edit(embed=embed, view=self)
        except discord.errors.NotFound:
            pass  # Message deleted


class RejectionReasonModal(Modal, title="Причина отклонения"):
    reason = TextInput(
        label="Почему задача отклонена?",
        placeholder="Объясните причину отклонения задачи...",
        required=True,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, task_id, original_message, submitter_id):
        super().__init__()
        self.task_id = task_id
        self.original_message = original_message
        self.submitter_id = submitter_id

    async def on_submit(self, interaction: discord.Interaction):
        update_task(self.task_id, {
            'Status': 'rejected',
            'RejectionReason': self.reason.value
        })

        # Update the original embed with red color and rejection info
        embed = Embed(
            title=f"Идея **{self.task_name}** отклонена",
            color=0xff0000
        )
        embed.add_field(name="Отправил", value=f"<@{self.submitter_id}>", inline=True)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)

        # Disable all buttons
        for item in self.original_message.components:
            if isinstance(item, View):
                for child in item.children:
                    child.disabled = True

        await self.original_message.edit(embed=embed, view=None)


class TaskSetupView(View):
    def __init__(self, task_id, original_message, submitter_id, task_name, task_description):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.original_message = original_message
        self.submitter_id = submitter_id
        self.task_name = task_name
        self.task_description = task_description
        self.selected_role = None
        self.priority_value = None
        self.deadline_value = None
        self.leadership_notes = None

        self._update_components()

    def _update_components(self):
        """Recreate components with current values"""
        self.clear_items()
        
        # Role select dropdown
        role_options = [
            SelectOption(label="Скриптер", value="Скриптер", default=self.selected_role == "Скриптер"),
            SelectOption(label="Билдер", value="Билдер", default=self.selected_role == "Билдер"),
            SelectOption(label="Аниматор", value="Аниматор", default=self.selected_role == "Аниматор"),
            SelectOption(label="Моделер", value="Моделер", default=self.selected_role == "Моделер"),
        ]
        role_select = Select(
            placeholder=f"Роль: {self.selected_role or 'Выберите...'}",
            options=role_options,
            custom_id=f"role_select_{self.task_id}"
        )
        role_select.callback = self.role_select_callback
        self.add_item(role_select)

        # Priority select dropdown
        priority_options = [
            SelectOption(label="Низкий", value="Низкий", default=self.priority_value == "Низкий"),
            SelectOption(label="Средний", value="Средний", default=self.priority_value == "Средний"),
            SelectOption(label="Высокий", value="Высокий", default=self.priority_value == "Высокий"),
        ]
        priority_select = Select(
            placeholder=f"{self.priority_value.capitalize() if self.priority_value else 'Выберите...'}",
            options=priority_options,
            custom_id=f"priority_select_{self.task_id}"
        )
        priority_select.callback = self.priority_select_callback
        self.add_item(priority_select)

        # Deadline button
        deadline_label = f"Дедлайн: {self.deadline_value}" if self.deadline_value else "Установить дедлайн"
        deadline_btn = Button(
            label=deadline_label,
            style=discord.ButtonStyle.secondary if not self.deadline_value else discord.ButtonStyle.success,
            custom_id=f"deadline_btn_{self.task_id}",
        )
        deadline_btn.callback = self.deadline_callback
        self.add_item(deadline_btn)

        # Notes button
        notes_label = "Заметки" if not self.leadership_notes else "Заметки"
        notes_btn = Button(
            label=notes_label,
            style=discord.ButtonStyle.secondary if not self.leadership_notes else discord.ButtonStyle.primary,
            custom_id=f"notes_btn_{self.task_id}",
        )
        notes_btn.callback = self.notes_callback
        self.add_item(notes_btn)

        # Submit button
        submit_btn = Button(
            label="Готово",
            style=discord.ButtonStyle.success,
            custom_id=f"submit_setup_{self.task_id}",
        )
        submit_btn.callback = self.submit_callback
        self.add_item(submit_btn)

    async def role_select_callback(self, interaction: discord.Interaction):
        self.selected_role = interaction.data['values'][0]
        self._update_components()
        await interaction.response.edit_message(view=self)

    async def priority_select_callback(self, interaction: discord.Interaction):
        self.priority_value = interaction.data['values'][0]
        self._update_components()
        await interaction.response.edit_message(view=self)

    async def deadline_callback(self, interaction: discord.Interaction):
        class DeadlineModal(Modal, title="Дедлайн (дней)"):
            days = TextInput(
                label="Количество дней",
                placeholder="3, 7, 14, 30...",
                required=False,
                max_length=10
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                parent = self.parent_view  # Get parent view reference
                if self.days.value:
                    try:
                        days = int(self.days.value)
                        if days < 1:
                            raise ValueError
                        parent.deadline_value = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
                        parent._update_components()
                        try:
                            await modal_interaction.response.edit_message(view=parent)
                        except discord.errors.NotFound:
                            pass
                    except ValueError:
                        try:
                            await modal_interaction.response.send_message(
                                "Неверное количество дней. Введите число больше 0",
                                ephemeral=True
                            )
                        except discord.errors.NotFound:
                            pass
                else:
                    parent.deadline_value = None
                    parent._update_components()
                    try:
                        await modal_interaction.response.edit_message(view=parent)
                    except discord.errors.NotFound:
                        pass

        modal = DeadlineModal()
        modal.parent_view = self  # Store parent view reference
        await interaction.response.send_modal(modal)

    async def notes_callback(self, interaction: discord.Interaction):
        class NotesModal(Modal, title="Заметки для разработчиков"):
            notes = TextInput(
                label="Заметки",
                placeholder="Опишите детали задачи, требования или пожелания...",
                required=False,
                max_length=500,
                style=discord.TextStyle.paragraph
            )

            async def on_submit(self, modal_interaction: discord.Interaction):
                parent = self.parent_view
                parent.leadership_notes = self.notes.value if self.notes.value else None
                parent._update_components()
                try:
                    await modal_interaction.response.edit_message(view=parent)
                except discord.errors.NotFound:
                    pass

        modal = NotesModal()
        modal.parent_view = self
        await interaction.response.send_modal(modal)

    async def submit_callback(self, interaction: discord.Interaction):
        if not self.selected_role:
            try:
                await interaction.response.send_message("Выберите роль", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        # Validate priority
        valid_priorities = ['Низкий', 'Средний', 'Высокий']
        if self.priority_value not in valid_priorities:
            try:
                await interaction.response.send_message(
                    f"Неверный приоритет. Используйте: {', '.join(valid_priorities)}",
                    ephemeral=True
                )
            except discord.errors.NotFound:
                pass
            return

        # Update task
        updates = {
            'Status': 'approved',
            'Role': self.selected_role,
            'Priority': self.priority_value
        }
        if self.deadline_value:
            updates['Deadline'] = self.deadline_value

        update_task(self.task_id, updates)

        # Update embed with green color
        embed = Embed(
            title=f"Идея **{self.task_name}** принята",
            color=0x00ff00
        )
        embed.add_field(name="Отправил", value=f"<@{self.submitter_id}>", inline=True)
        embed.add_field(name="Роль", value=self.selected_role, inline=True)
        embed.add_field(name="Приоритет", value=self.priority_value, inline=True)
        embed.add_field(name="Дедлайн", value=self.deadline_value or "Не указан", inline=True)

        # Disable all buttons
        try:
            await self.original_message.edit(embed=embed, view=None)
        except discord.errors.NotFound:
            pass  # Message deleted

        # Send to tasks channel for developers to claim
        tasks_channel = bot.get_channel(config.TASKS_CHANNEL_ID) if config.TASKS_CHANNEL_ID else None
        if tasks_channel:
            task_embed = Embed(
                title=f"{self.task_name}",
                description=self.task_description,
                color=0x3498db
            )
            task_embed.add_field(name="Роль", value=self.selected_role, inline=True)
            task_embed.add_field(name="Приоритет", value=self.priority_value.capitalize(), inline=True)
            if self.deadline_value:
                task_embed.add_field(name="Дедлайн", value=self.deadline_value, inline=True)
            if self.leadership_notes:
                task_embed.add_field(name="Заметки", value=self.leadership_notes, inline=False)
            task_embed.set_footer(text="Нажмите кнопку чтобы взять задачу")

            claim_view = TaskClaimView(
                self.task_id,
                self.task_name,
                self.task_description,
                self.selected_role,
                self.priority_value,
                self.deadline_value,
                self.leadership_notes
            )

            await tasks_channel.send(embed=task_embed, view=claim_view)

        # Delete the setup message and send confirmation
        try:
            await interaction.message.delete()
        except discord.errors.NotFound:
            pass  # Message already deleted
        
        try:
            await interaction.followup.send("Задача отправлена разработчикам", ephemeral=True)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            # Interaction expired or already responded - task was already processed successfully
            pass


class IdeaReviewView(View):
    def __init__(self, task_id, submitter_id, task_name, task_description):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.submitter_id = submitter_id
        self.task_name = task_name
        self.task_description = task_description

        # Accept button
        self.accept_btn = Button(
            label="Принять",
            style=discord.ButtonStyle.green,
            custom_id=f"accept_{task_id}",
        )
        self.accept_btn.callback = self.accept_callback

        # Reject button
        self.reject_btn = Button(
            label="Отклонить",
            style=discord.ButtonStyle.red,
            custom_id=f"reject_{task_id}",
        )
        self.reject_btn.callback = self.reject_callback

        self.add_item(self.accept_btn)
        self.add_item(self.reject_btn)

    async def accept_callback(self, interaction: discord.Interaction):
        # Check if already processed
        tasks = get_tasks()
        task = next((t for t in tasks if t['ID'] == self.task_id), None)
        if task and task['Status'] != 'pending_approval':
            try:
                await interaction.response.send_message("Задача уже была обработана.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        # Send task setup view (ephemeral - visible only to leadership)
        view = TaskSetupView(self.task_id, interaction.message, self.submitter_id, self.task_name, self.task_description)
        try:
            await interaction.response.send_message(
                content="**Настройка задачи**\nВыберите роль, приоритет и дедлайн:",
                view=view,
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass

    async def reject_callback(self, interaction: discord.Interaction):
        # Check if already processed
        tasks = get_tasks()
        task = next((t for t in tasks if t['ID'] == self.task_id), None)
        if task and task['Status'] != 'pending_approval':
            try:
                await interaction.response.send_message("Задача уже была обработана.", ephemeral=True)
            except discord.errors.NotFound:
                pass
            return

        # Show rejection modal
        modal = RejectionReasonModal(self.task_id, interaction.message, self.submitter_id)
        await interaction.response.send_modal(modal)


class SubmitIdeaModal(Modal, title="Предложить идею задачи"):
    task_name = TextInput(
        label="Название задачи",
        placeholder="Краткое название задачи",
        required=True,
        max_length=100
    )
    task_description = TextInput(
        label="Описание задачи",
        style=discord.TextStyle.paragraph,
        placeholder="Подробное описание задачи",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        task_id = add_task(
            self.task_name.value,
            self.task_description.value,
            str(interaction.user.id),
            str(interaction.user.id)
        )

        # Create embed for approval channel
        embed = Embed(
            title=self.task_name.value,
            description=self.task_description.value,
            color=0x3498db
        )
        embed.add_field(name="Отправил", value=f"{interaction.user.mention}", inline=True)
        embed.add_field(name="Статус", value="Ожидает решения", inline=True)

        # Send to approval channel with buttons
        approval_channel = bot.get_channel(config.APPROVAL_CHANNEL_ID)
        if approval_channel:
            view = IdeaReviewView(task_id, str(interaction.user.id), self.task_name.value, self.task_description.value)
            await approval_channel.send(embed=embed, view=view)

        try:
            await interaction.response.send_message(
                f"Идея **{self.task_name.value}** отправлена на рассмотрение",
                ephemeral=True
            )
        except discord.errors.NotFound:
            pass  # Interaction expired


class SubmitIdeaView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Отправить идею для разработки",
        style=discord.ButtonStyle.blurple,
        custom_id="submit_idea_button",
    )
    async def submit_idea_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SubmitIdeaModal()
        await interaction.response.send_modal(modal)


@bot.event
async def on_ready():
    print(f'Bot is ready. Logged in as {bot.user}')
    print(f'Connected to {len(bot.guilds)} guild(s)')

    # Initialize Google Sheets headers
    init_sheet_headers()

    if config.GUILD_ID:
        guild = discord.Object(id=config.GUILD_ID)
        try:
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            print(f'Slash commands synced for guild {config.GUILD_ID}, count={len(synced)}')
            print(f'Synced commands: {[cmd.name for cmd in synced]}')
        except Exception as e:
            print(f'Error syncing commands: {e}')

    # Send idea submission message to IDEAS_CHANNEL
    ideas_channel = bot.get_channel(config.IDEAS_CHANNEL_ID)
    if ideas_channel:
        try:
            # Delete old bot messages
            async for message in ideas_channel.history(limit=50):
                if message.author == bot.user:
                    await message.delete()
                    print(f'Deleted old message from ideas channel')

            # Send new embed message with button
            embed = Embed(
                title="Предложить идею для разработки",
                description="Нажмите кнопку ниже, чтобы предложить новую идею для разработки.",
                color=0x3498db
            )
            embed.set_footer(text="Ваша идея будет рассмотрена руководством")
            view = SubmitIdeaView()
            await ideas_channel.send(embed=embed, view=view)
            print('Sent ideas submission message to IDEAS_CHANNEL')
        except Exception as e:
            print(f'Error sending ideas message: {e}')


@bot.event
async def on_message(message):
    # Don't handle bot messages
    if message.author == bot.user:
        return

    # Check if this message is a reply to a pending task completion
    if message.reference and message.reference.message_id:
        ref_message_id = message.reference.message_id
        if ref_message_id in pending_completions and message.attachments:
            pending = pending_completions[ref_message_id]

            # Verify the user is the same who selected the task
            if message.author.id != pending['user_id']:
                return

            # Get the attachment
            attachment = await message.attachments[0].to_file()

            # Update task status
            update_task(pending['task_id'], {'Status': 'review'})

            # Save completion record to Google Sheets
            add_completion_record(
                task_id=pending['task_id'],
                user_id=str(message.author.id),
                user_name=str(message.author),
                filename=attachment.filename
            )

            # Create completion embed
            embed = Embed(
                title=f"Задача завершена: {pending['task_name']}",
                description="Файл прикреплен к сообщению",
                color=0x00ff00
            )
            embed.add_field(name="Выполнил", value=f"{message.author.mention}", inline=True)
            embed.add_field(name="ID задачи", value=pending['task_id'], inline=True)

            # Send to approval channel
            leadership_channel = bot.get_channel(config.APPROVAL_CHANNEL_ID)
            if leadership_channel:
                await leadership_channel.send(
                    f"Задача завершена {message.author.mention} и готова к проверке.",
                    embed=embed,
                    file=attachment,
                    view=TaskReviewView(pending['task_id'], pending['task_name'], str(message.author.id))
                )

            # Clean up
            del pending_completions[ref_message_id]

            await message.channel.send(
                f"{message.author.mention}, задача **{pending['task_name']}** отправлена на проверку.",
                delete_after=5
            )


# Commands
@tree.command(name="my_tasks", description="View your assigned tasks")
async def my_tasks(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    tasks = get_tasks()
    user_tasks = [t for t in tasks if str(t.get('Assignee', '')) == str(interaction.user.id) and t['Status'] == 'in_progress']
    if not user_tasks:
        await interaction.followup.send("Нет задач.", ephemeral=True)
        return
    embed = Embed(title="Ваши задачи", description="Выберите задачу из dropdown чтобы завершить", color=0x00ff00)
    for task in user_tasks:
        embed.add_field(name="Задача", value=f"**{task['Name']}**\nПриоритет: {task['Priority']}\nДедлайн: {task['Deadline']}", inline=False)
    view = TaskView(user_tasks, interaction.user)
    await interaction.followup.send(embed=embed, view=view)

bot.run(config.DISCORD_TOKEN)