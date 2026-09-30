from .jobfinder import JobFinder

async def setup(bot):
    await bot.add_cog(JobFinder(bot))
